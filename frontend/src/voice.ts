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

    try {
      const recognition = new this.SpeechRecognitionClass();
      this.currentRecognition = recognition;
      recognition.continuous = false;
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

        const combined = [finalAccumulator, interimAccumulator].filter(Boolean).join(' ').trim();
        if (combined) {
          onResult(combined);
        }
      };

      recognition.onerror = (event: any) => {
        if (this.activeSessionId !== sessionId) return;
        this.listening = false;
        const err = event?.error || 'unknown';

        if (err === 'not-allowed' || err === 'service-not-allowed') {
          onError('Microphone access was denied. You can continue typing in the chat input.');
        } else if (err === 'no-speech') {
          onError('No speech was detected. Please try speaking again.');
        } else if (err === 'network') {
          onError('Speech recognition network error. Please check your connection or type your message.');
        } else {
          onError(`Speech recognition notice: ${err}`);
        }
        onEnd();
      };

      recognition.onend = () => {
        if (this.activeSessionId !== sessionId) return;
        this.listening = false;
        onEnd();
      };

      recognition.start();
    } catch {
      this.listening = false;
      onError('Could not start microphone. Please try again.');
      onEnd();
    }
  }

  stop(): void {
    const prev = this.currentRecognition;
    this.activeSessionId++;
    this.listening = false;
    this.currentRecognition = null;

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
