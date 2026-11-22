import test from 'node:test';
import assert from 'node:assert/strict';
import { BrowserSpeechRecognitionProvider } from '../src/voice.ts';

// Mock SpeechRecognition class
class MockSpeechRecognition {
  constructor() {
    this.continuous = false;
    this.interimResults = false;
    this.lang = 'en-US';
    this.onresult = null;
    this.onerror = null;
    this.onend = null;
    this.started = false;
  }

  start() {
    this.started = true;
  }

  stop() {
    this.started = false;
    if (this.onend) this.onend();
  }

  // Helper to emit speech result
  emitResults(results) {
    if (this.onresult) {
      this.onresult({ results });
    }
  }

  // Helper to emit error
  emitError(error) {
    if (this.onerror) {
      this.onerror({ error });
    }
  }
}

test('BrowserSpeechRecognitionProvider: reports unsupported when no recognition class is available', () => {
  const provider = new BrowserSpeechRecognitionProvider(null);
  assert.equal(provider.isSupported(), false);

  let errorReported = '';
  provider.start(
    () => {},
    (err) => { errorReported = err; },
    () => {}
  );
  assert.match(errorReported, /not supported/i);
});

test('BrowserSpeechRecognitionProvider: interim results update cleanly without multiplying words', () => {
  const provider = new BrowserSpeechRecognitionProvider(MockSpeechRecognition);
  assert.equal(provider.isSupported(), true);

  const transcripts = [];
  provider.start(
    (t) => transcripts.push(t),
    (err) => assert.fail(`Unexpected error: ${err}`),
    () => {}
  );

  assert.equal(provider.isListening(), true);
  assert.equal(provider.currentRecognition.continuous, true);

  // Simulate speaking "I lost my card" with progressive interim results
  // Event 1: interim "I"
  provider.currentRecognition.emitResults([
    [{ transcript: 'I' }]
  ]);
  assert.equal(transcripts[transcripts.length - 1], 'I');

  // Event 2: interim "I lost"
  provider.currentRecognition.emitResults([
    [{ transcript: 'I lost' }]
  ]);
  assert.equal(transcripts[transcripts.length - 1], 'I lost');

  // Event 3: interim "I lost my"
  provider.currentRecognition.emitResults([
    [{ transcript: 'I lost my' }]
  ]);
  assert.equal(transcripts[transcripts.length - 1], 'I lost my');

  // Event 4: final "I lost my card"
  const finalItem = [{ transcript: 'I lost my card' }];
  finalItem.isFinal = true;
  provider.currentRecognition.emitResults([finalItem]);

  // The final transcript must be exactly "I lost my card", NEVER duplicated
  assert.equal(transcripts[transcripts.length - 1], 'I lost my card');
  assert.ok(!transcripts[transcripts.length - 1].includes('I I lost'));
});

test('BrowserSpeechRecognitionProvider: a short pause restarts listening and preserves prior words', async () => {
  const provider = new BrowserSpeechRecognitionProvider(MockSpeechRecognition);
  const transcripts = [];
  let ended = 0;

  provider.start(
    (t) => transcripts.push(t),
    (err) => assert.fail(`Unexpected error: ${err}`),
    () => { ended++; }
  );

  const firstSegment = provider.currentRecognition;
  const firstFinal = [{ transcript: 'Please block my card' }];
  firstFinal.isFinal = true;
  firstSegment.emitResults([firstFinal]);
  firstSegment.onend();

  assert.equal(provider.isListening(), true);
  assert.equal(ended, 0);
  await new Promise((resolve) => setTimeout(resolve, 300));
  assert.notEqual(provider.currentRecognition, firstSegment);

  const secondFinal = [{ transcript: 'and replace it' }];
  secondFinal.isFinal = true;
  provider.currentRecognition.emitResults([secondFinal]);
  assert.equal(transcripts.at(-1), 'Please block my card and replace it');

  provider.stop();
  assert.equal(provider.isListening(), false);
});

test('BrowserSpeechRecognitionProvider: duplicate callbacks do not duplicate final text', () => {
  const provider = new BrowserSpeechRecognitionProvider(MockSpeechRecognition);
  const transcripts = [];

  provider.start(
    (t) => transcripts.push(t),
    () => {},
    () => {}
  );

  const finalItem = [{ transcript: 'Show my accounts' }];
  finalItem.isFinal = true;

  // Emit same final result twice
  provider.currentRecognition.emitResults([finalItem]);
  provider.currentRecognition.emitResults([finalItem]);

  assert.equal(transcripts[transcripts.length - 1], 'Show my accounts');
});

test('BrowserSpeechRecognitionProvider: rapid button clicks and start/stop cancel previous session safely', () => {
  const provider = new BrowserSpeechRecognitionProvider(MockSpeechRecognition);
  let ended = 0;

  // Rapid start -> start -> stop
  provider.start(() => {}, () => {}, () => { ended++; });
  const firstInstance = provider.currentRecognition;

  provider.start(() => {}, () => {}, () => { ended++; });
  const secondInstance = provider.currentRecognition;

  assert.notEqual(firstInstance, secondInstance);
  assert.equal(provider.isListening(), true);

  // Events from the discarded first instance must be ignored
  let lateTranscript = '';
  firstInstance.onresult = () => { lateTranscript = 'ignored'; };
  firstInstance.emitResults([[{ transcript: 'late text' }]]);
  assert.equal(lateTranscript, 'ignored');

  provider.stop();
  assert.equal(provider.isListening(), false);
});

test('BrowserSpeechRecognitionProvider: handles permission denied and recognition errors gracefully', () => {
  const provider = new BrowserSpeechRecognitionProvider(MockSpeechRecognition);
  let errorMsg = '';
  let endedCalled = false;

  provider.start(
    () => {},
    (err) => { errorMsg = err; },
    () => { endedCalled = true; }
  );

  provider.currentRecognition.emitError('not-allowed');

  assert.match(errorMsg, /denied/i);
  assert.equal(endedCalled, true);
  assert.equal(provider.isListening(), false);
});
