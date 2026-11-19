"""Authoritative SQLite cards and atomic, durable retry outcomes."""
import sqlite3
from contextlib import closing
from functools import wraps
from pathlib import Path
from uuid import uuid4
from card_servicing.idempotency import IdempotencyConflict, fingerprint
from card_servicing.models import Card, CardCreate, CardIssuance
from card_servicing.ports import StorageUnavailable, AccountNotOwned, BlockedCardExists


def storage_operation(operation):
    """Translate expected provider failures at the adapter boundary only."""
    @wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (sqlite3.Error, OSError, ValueError):
            raise StorageUnavailable('Card storage unavailable') from None
    return wrapped

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_statuses (code TEXT PRIMARY KEY, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
    created_at TEXT NOT NULL,
    user_name TEXT NOT NULL CHECK(length(trim(user_name)) > 0),
    given_name TEXT NOT NULL CHECK(length(trim(given_name)) > 0),
    account_number TEXT NOT NULL CHECK(length(trim(account_number)) > 0),
    card_number TEXT NOT NULL UNIQUE CHECK(length(card_number) = 36 AND substr(card_number,1,4) = 'SIM-'),
    status TEXT NOT NULL REFERENCES card_statuses(code)
);
CREATE INDEX IF NOT EXISTS ix_card_created_at ON cards(created_at);
CREATE TABLE IF NOT EXISTS card_idempotency (
    idempotency_key TEXT PRIMARY KEY NOT NULL,
    request_fingerprint TEXT NOT NULL,
    card_id TEXT NOT NULL UNIQUE REFERENCES cards(id),
    response_status_code INTEGER NOT NULL CHECK(response_status_code = 201),
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cards_account ON cards(account_number, created_at, id);
CREATE TABLE IF NOT EXISTS card_issuance_requests (
    request_key TEXT PRIMARY KEY NOT NULL,
    request_fingerprint TEXT NOT NULL,
    card_id TEXT NOT NULL REFERENCES cards(id),
    response_json TEXT NOT NULL
);
"""

class SQLiteCardStore:
    dependency_name = 'sqlite'

    def __init__(self, path: str):
        self.path = Path(path)
        self.ready = False

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        try:
            db.execute('PRAGMA foreign_keys = ON')
        except Exception:
            db.close()
            raise
        return db

    @storage_operation
    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA user_version').fetchone()[0] > 3:
                raise StorageUnavailable('Unsupported future database schema')
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
            db.execute("INSERT OR IGNORE INTO card_statuses VALUES ('created', 'Simulated card created; no issuer integration')")
            db.execute("INSERT OR IGNORE INTO card_statuses VALUES ('blocked', 'Card blocked by financial crime / customer request')")
            from card_servicing.banking import SCHEMA as BANKING_SCHEMA
            for statement in BANKING_SCHEMA.split(';'):
                if statement.strip():
                    db.execute(statement)
            db.execute('PRAGMA user_version = 3')
        self.ready = True

    @storage_operation
    def check_readiness(self):
        if not self.ready:
            return False
        uri = self.path.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as db, db:
            db.execute('BEGIN')
            db.execute('SELECT id, created_at, user_name, given_name, account_number, card_number, status FROM cards LIMIT 1').fetchone()
            db.execute('SELECT idempotency_key, request_fingerprint, card_id, response_status_code, response_json, created_at FROM card_idempotency LIMIT 1').fetchone()
            db.execute('SELECT id,username,password_hash FROM customers LIMIT 1').fetchone()
            db.execute('SELECT account_number,customer_id,account_type FROM accounts LIMIT 1').fetchone()
            db.execute('SELECT card_id,account_number FROM account_cards LIMIT 1').fetchone()
            db.execute('SELECT customer_id,request_key,fingerprint,account_number FROM banking_requests LIMIT 1').fetchone()
            db.execute('SELECT request_key,request_fingerprint,card_id,response_json FROM card_issuance_requests LIMIT 1').fetchone()
            return db.execute("SELECT code FROM card_statuses WHERE code = 'created'").fetchone() is not None

    def create(self, instruction: CardCreate) -> Card:
        return self.create_idempotent(instruction, str(uuid4()))[0]

    @storage_operation
    def create_idempotent(self, instruction: CardCreate, key: str) -> tuple[Card, bool]:
        if not self.ready:
            self.initialize()
        with closing(self.connect()) as db, db:
            # Serialize key lookup and both inserts across processes as well as threads.
            db.execute('BEGIN IMMEDIATE')
            return self._create_idempotent(db, instruction, key)

    def _create_idempotent(self, db, instruction, key):
        """Original card generation and snapshot, inside the caller's transaction."""
        identity = fingerprint(instruction)
        existing = db.execute('SELECT request_fingerprint, response_json FROM card_idempotency WHERE idempotency_key = ?', (key,)).fetchone()
        if existing:
            if existing[0] != identity:
                raise IdempotencyConflict()
            return Card.model_validate_json(existing[1]), True
        card = Card(id=uuid4(), card_number='SIM-' + uuid4().hex, **instruction.model_dump())
        db.execute('INSERT INTO cards (id, created_at, user_name, given_name, account_number, card_number, status) VALUES (?, ?, ?, ?, ?, ?, ?)',
                   (str(card.id), card.created_at.isoformat(), card.user_name, card.given_name, card.account_number, card.card_number, card.status))
        db.execute('INSERT INTO card_idempotency VALUES (?, ?, ?, ?, ?, ?)',
                   (key, identity, str(card.id), 201, card.model_dump_json(), card.created_at.isoformat()))
        return card, False

    @storage_operation
    def issue_idempotent(self, instruction, key, *, additional=False, customer_id=None):
        if not self.ready:
            self.initialize()
        identity = fingerprint(instruction) + (':additional' if additional else ':initial')
        with closing(self.connect()) as db, db:
            # One writer reservation covers ownership, replay, existence and creation.
            # Independent service processes/channels therefore observe the same decision.
            db.execute('BEGIN IMMEDIATE')
            if customer_id is not None and not db.execute(
                'SELECT 1 FROM accounts WHERE account_number=? AND customer_id=?',
                (instruction.account_number, customer_id)).fetchone():
                raise AccountNotOwned()
            prior = db.execute('SELECT request_fingerprint,response_json FROM card_issuance_requests WHERE request_key=?', (key,)).fetchone()
            if prior:
                if prior[0] != identity:
                    raise IdempotencyConflict()
                return CardIssuance.model_validate_json(prior[1]), True
            # Honor pre-migration card snapshots before checking current existence.
            old = db.execute('SELECT request_fingerprint,response_json FROM card_idempotency WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if old[0] != fingerprint(instruction) or additional:
                    raise IdempotencyConflict()
                result = CardIssuance.for_card(Card.model_validate_json(old[1]), 'created')
                replayed = True
            else:
                if db.execute("SELECT 1 FROM cards WHERE account_number=? AND status='blocked'",
                              (instruction.account_number,)).fetchone():
                    raise BlockedCardExists()
                db.row_factory = sqlite3.Row
                query = 'SELECT * FROM cards WHERE account_number=?'
                args = [instruction.account_number]
                if customer_id is None:
                    query += ' AND user_name=?'
                    args.append(instruction.user_name)
                row = db.execute(query + ' ORDER BY created_at,id LIMIT 1', args).fetchone()
                if row and not additional:
                    result = CardIssuance.for_card(Card.model_validate(dict(row)), 'already_exists')
                else:
                    card, _ = self._create_idempotent(db, instruction, key)
                    result = CardIssuance.for_card(card, 'additional_created' if additional else 'created')
                replayed = False
            db.execute('INSERT INTO card_issuance_requests VALUES(?,?,?,?)',
                       (key, identity, str(result.id), result.model_dump_json()))
            if customer_id is not None:
                db.execute('INSERT OR IGNORE INTO account_cards VALUES(?,?)', (str(result.id), instruction.account_number))
            return result, replayed

    @storage_operation
    def replace_idempotent(self, card_id, key, *, customer_id=None, authorize=None):
        if not self.ready:
            self.initialize()
        identity = f"replace:{card_id}"
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if authorize:
                authorize(db)  # Same transaction as issuance: no declaration race.
            db.row_factory = sqlite3.Row
            target_row = db.execute('SELECT * FROM cards WHERE id=? OR card_number=?', (str(card_id), str(card_id))).fetchone()
            if not target_row:
                raise ValueError("Card not found")
            account_number = target_row['account_number']
            if customer_id is not None and not db.execute(
                'SELECT 1 FROM accounts WHERE account_number=? AND customer_id=?',
                (account_number, customer_id)).fetchone():
                raise AccountNotOwned()
            if target_row['status'] != 'blocked':
                raise ValueError("Only blocked cards can be replaced")
            prior = db.execute('SELECT request_fingerprint,response_json FROM card_issuance_requests WHERE request_key=?', (key,)).fetchone()
            if prior:
                if prior[0] != identity:
                    raise IdempotencyConflict()
                return CardIssuance.model_validate_json(prior[1]), True
            instruction = CardCreate(
                user_name=target_row['user_name'],
                given_name=target_row['given_name'],
                account_number=account_number,
            )
            card, _ = self._create_idempotent(db, instruction, key)
            result = CardIssuance.for_card(card, 'replacement_created')
            db.execute('INSERT INTO card_issuance_requests VALUES(?,?,?,?)',
                       (key, identity, str(result.id), result.model_dump_json()))
            if customer_id is not None:
                db.execute('INSERT OR IGNORE INTO account_cards VALUES(?,?)', (str(result.id), account_number))
            return result, False

    @storage_operation
    def update_card_status(self, card_id: str, status: str) -> bool:
        if not self.ready:
            self.initialize()
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            cur = db.execute('UPDATE cards SET status=? WHERE id=? OR card_number=?', (status, str(card_id), str(card_id)))
            return cur.rowcount > 0

    @storage_operation
    def has_blocked_cards_for_account(self, account_number: str) -> bool:
        if not self.ready:
            self.initialize()
        with closing(self.connect()) as db:
            row = db.execute("SELECT 1 FROM cards WHERE account_number=? AND status='blocked'", (account_number,)).fetchone()
            return row is not None

    @storage_operation
    def get_card(self, card_id: str):
        if not self.ready:
            self.initialize()
        with closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM cards WHERE id=? OR card_number=?', (str(card_id), str(card_id))).fetchone()
            return Card.model_validate(dict(row)) if row else None
