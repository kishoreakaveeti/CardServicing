/**
 * Voice Input service with Web Speech API feature detection and fallback.
 * Follows an injectable interface for future Azure Speech SDK or custom backend integration.
 */

export interface TranscriptionProvider {
  isSupported(): boolean;
  start(onResult: (transcript: string) => void, onError: (error: string) => void, onEnd: () => void): void;
  stop(): void;
  isListening(): boolean;
}

// Browser native Web Speech API implementation with robust state handling
export class BrowserSpeechRecognitionProvider implements TranscriptionProvider {
  private activeSessionId: number = 0;
  private listening: boolean = false;
  private currentRecognition: any = null;
  private SpeechRecognitionClass: any = null;
  private restartTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(customRecognitionClass?: any) {
    if (customRecognitionClass) {
      this.SpeechRecognitionClass = customRecognitionClass;
    } else if (typeof window !== 'undefined') {
      this.SpeechRecognitionClass =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition || null;
    }
  }

  isSupported(): boolean {
    return this.SpeechRecognitionClass !== null;
  }

  isListening(): boolean {
    return this.listening;
  }

  start(
    onResult: (transcript: string) => void,
    onError: (error: string) => void,
    onEnd: () => void
  ): void {
    if (!this.isSupported()) {
      onError('Speech recognition is not supported in this browser. You can type your request in chat.');
      return;
    }

    // Stop any existing session before starting a new one
    if (this.listening) {
      this.stop();
    }

    const sessionId = ++this.activeSessionId;
    this.listening = true;

    let committedTranscript = '';
    let segmentTranscript = '';
    let ended = false;

    const finish = (error?: string) => {
      if (ended || this.activeSessionId !== sessionId) return;
      ended = true;
      this.listening = false;
      this.currentRecognition = null;
      if (this.restartTimer) {
        clearTimeout(this.restartTimer);
        this.restartTimer = null;
      }
      if (error) onError(error);
      onEnd();
    };

    const beginSegment = () => {
      if (!this.listening || ended || this.activeSessionId !== sessionId) return;
      segmentTranscript = '';
      let recognition: any;
      try {
        recognition = new this.SpeechRecognitionClass();
        this.currentRecognition = recognition;
        // Keep the browser listening across ordinary sentence pauses. Some Web
        // Speech implementations still end individual recognition segments, so
        // onend below safely starts another segment until the user presses Stop.
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = 'en-US';

        recognition.onresult = (event: any) => {
        if (this.activeSessionId !== sessionId) return;

        let finalAccumulator = '';
        let interimAccumulator = '';

        const results = event.results || [];
        for (let i = 0; i < results.length; ++i) {
          const item = results[i];
          const text = (item && item[0] && item[0].transcript ? item[0].transcript : '').trim();
          if (!text) continue;

          if (item.isFinal) {
            finalAccumulator = finalAccumulator ? `${finalAccumulator} ${text}` : text;
          } else {
            interimAccumulator = interimAccumulator ? `${interimAccumulator} ${text}` : text;
          }
        }

        segmentTranscript = [finalAccumulator, interimAccumulator].filter(Boolean).join(' ').trim();
        const combined = [committedTranscript, segmentTranscript].filter(Boolean).join(' ').trim();
        if (combined) {
          onResult(combined);
        }
        };

        recognition.onerror = (event: any) => {
        if (this.activeSessionId !== sessionId) return;
        const err = event?.error || 'unknown';

        if (err === 'not-allowed' || err === 'service-not-allowed') {
          finish('Microphone access was denied. You can continue typing in the chat input.');
        } else if (err === 'no-speech') {
          // A short pause is not an error. The following onend event restarts
          // recognition while preserving everything already transcribed.
          return;
        } else if (err === 'network') {
          finish('Speech recognition network error. Please check your connection or type your message.');
        } else {
          finish(`Speech recognition notice: ${err}`);
        }
        };

        recognition.onend = () => {
        if (this.activeSessionId !== sessionId || ended || !this.listening) return;
        if (segmentTranscript) {
          committedTranscript = [committedTranscript, segmentTranscript].filter(Boolean).join(' ').trim();
          segmentTranscript = '';
        }
        this.currentRecognition = null;
        this.restartTimer = setTimeout(() => {
          this.restartTimer = null;
          beginSegment();
        }, 250);
        };

        recognition.start();
      } catch {
        finish('Could not continue microphone recognition. Please try again.');
      }
    };

    beginSegment();
  }

  stop(): void {
    const prev = this.currentRecognition;
    this.activeSessionId++;
    this.listening = false;
    this.currentRecognition = null;
    if (this.restartTimer) {
      clearTimeout(this.restartTimer);
      this.restartTimer = null;
    }

    if (prev) {
      try {
        prev.onresult = null;
        prev.onerror = null;
        prev.onend = null;
        prev.stop();
      } catch {
        // Safe disposal
      }
    }
  }
}

export const defaultSpeechProvider: TranscriptionProvider = new BrowserSpeechRecognitionProvider();
