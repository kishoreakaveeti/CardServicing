"""Source navigation must move with code and fail clearly for renamed methods."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/generate_debugging_guide.py"
SPEC = importlib.util.spec_from_file_location("debugging_guide_generator", SCRIPT)
guide = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guide
SPEC.loader.exec_module(guide)


class DebuggingGuideTests(unittest.TestCase):
    def test_python_qualified_names_skip_docstrings_and_multiline_signatures(self):
        source = '''class Service:
    async def execute(
        self,
        value,
    ):
        """An explanation.
        With another line.
        """
        # First statement follows this comment.
        return value

def create_app():
    def execute():
        return "handler"
    return execute
'''
        symbols = guide.python_symbols(source)
        self.assertEqual(symbols["Service.execute"].executable, 10)
        self.assertEqual(symbols["create_app.execute"].executable, 14)
        self.assertEqual(symbols["Service.execute"].statement, "return value")
        moved = guide.python_symbols("# inserted code\n\n" + source)
        self.assertEqual(moved["Service.execute"].executable, 12)
        self.assertEqual(moved["create_app.execute"].executable, 16)

    def test_ambiguous_python_name_fails(self):
        with self.assertRaisesRegex(ValueError, "Ambiguous symbol"):
            guide.python_symbols("def run():\n    pass\ndef run():\n    pass\n")

    def test_typescript_named_handler_moves_and_skips_comments(self):
        source = '''function App() {
  async function request(action: Pending) {
    // Validation before request.
    /* A longer
       comment. */

    setBusy(true);
  }
}
'''
        self.assertEqual(guide.typescript_symbol(source, "App.request").executable, 7)
        self.assertEqual(guide.typescript_symbol("\n\n" + source, "App.request").executable, 9)
        with self.assertRaisesRegex(ValueError, "Missing/ambiguous"):
            guide.typescript_symbol(source, "App.renamed")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            guide.typescript_symbol(source, "Other.request")

    def test_named_resolution_generates_new_links_and_rejects_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "service.py"
            source.write_text("def handler():\n    return 1\n", encoding="utf-8")
            manifest = {
                "symbols": {"handler": {"file": "service.py", "symbol": "handler", "group": "Test"}},
                "journeys": [{"id": "test", "title": "Test", "trigger": "Run", "outcome": "Result",
                              "troubleshoot": "Inspect", "steps": [{"layer": "Test", "title": "Call",
                              "refs": ["handler"], "inspect": "Observe"}]}],
            }
            original = guide.render(manifest, guide.resolve_symbols(root, manifest))
            self.assertIn("service.py#L2", original)
            source.write_text("\n\ndef handler():\n    return 1\n", encoding="utf-8")
            updated = guide.render(manifest, guide.resolve_symbols(root, manifest))
            self.assertIn("service.py#L4", updated)
            self.assertNotEqual(original, updated)
            source.write_text("def renamed():\n    return 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Cannot resolve handler"):
                guide.resolve_symbols(root, manifest)

    def test_generation_preserves_handwritten_text_and_check_detects_staleness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "service.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
            manifest = {"version": 1, "symbols": {"test": {
                "file": "service.py", "symbol": "handler", "group": "Test"}}, "journeys": []}
            (root / guide.MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
            target = root / guide.GUIDE
            target.write_text(f"# Keep intro\n{guide.START}\n{guide.END}\nKeep tail\n", encoding="utf-8")
            with patch.object(guide, "ROOT", root):
                self.assertEqual(guide.main([]), 0)
                saved = target.read_bytes()
                self.assertTrue(saved.startswith(b"# Keep intro\n"))
                self.assertTrue(saved.endswith(b"Keep tail\n"))
                self.assertEqual(guide.main(["--check"]), 0)
                (root / "service.py").write_text("\ndef handler():\n    return 1\n", encoding="utf-8")
                self.assertEqual(guide.main(["--check"]), 1)
                self.assertEqual(target.read_bytes(), saved)
                self.assertEqual(guide.main([]), 0)
                self.assertEqual(guide.main(["--check"]), 0)

    def test_recursive_segments_and_missing_markers_fail(self):
        with self.assertRaisesRegex(ValueError, "Recursive"):
            guide.expand_steps([{"segment": "loop"}], {"loop": [{"segment": "loop"}]})
        with self.assertRaisesRegex(ValueError, "marker"):
            guide.update_document("No markers", "new output")

    def test_annotated_prose_links_refresh_path_and_line_without_changing_label(self):
        original = "See [Friendly method](../old.py#L99)<!-- debug-symbol: handler -->."
        manifest = {"symbols": {"handler": {"file": "moved.py"}}}
        updated = guide.refresh_annotated_links(original, manifest, {"handler": guide.Location(2, 5, "pass")})
        self.assertEqual(updated, "See [Friendly method](../moved.py#L5)<!-- debug-symbol: handler -->.")
        with self.assertRaises(KeyError):
            guide.refresh_annotated_links(original, {"symbols": {}}, {})

    def test_repository_manifest_and_generated_guide_are_current(self):
        manifest = json.loads((guide.ROOT / guide.MANIFEST).read_text(encoding="utf-8"))
        self.assertTrue(all("line" not in entry for entry in manifest["symbols"].values()))
        locations = guide.resolve_symbols(guide.ROOT, manifest)
        document = (guide.ROOT / guide.GUIDE).read_text(encoding="utf-8")
        generated = guide.update_document(document, guide.render(manifest, locations))
        self.assertEqual(document, guide.refresh_annotated_links(generated, manifest, locations))


if __name__ == "__main__":
    unittest.main()
