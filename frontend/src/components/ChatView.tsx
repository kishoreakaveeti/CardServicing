import React, { useEffect, useRef, useState } from 'react';
import { ChatMessage, InputTool, SelectionOption } from '../types';
import { TranscriptionProvider, defaultSpeechProvider } from '../voice';

interface ChatViewProps {
  messages: ChatMessage[];
  isBusy: boolean;
  onSendMessage: (text: string) => Promise<void>;
  onConfirmAction: () => Promise<void>;
  onCancelAction?: () => void;
  onSelectCountry: (countryCode: string) => Promise<void>;
  onActionChipClick: (action: string) => void | Promise<void>;
  onExploreProducts: () => void;
  tools: InputTool[];
  activeTool: string;
  onSupplyInputs: (tool: string, fields: Record<string, string>) => Promise<void>;
  transcriptionProvider?: TranscriptionProvider;
}

const COMMON_COUNTRIES: SelectionOption[] = [
  { ref: 'AU', label: 'Australia (AU)' },
  { ref: 'US', label: 'United States (US)' },
  { ref: 'GB', label: 'United Kingdom (GB)' },
  { ref: 'NZ', label: 'New Zealand (NZ)' },
  { ref: 'SG', label: 'Singapore (SG)' },
  { ref: 'JP', label: 'Japan (JP)' },
  { ref: 'KR', label: 'South Korea (KR)' },
  { ref: 'KP', label: 'North Korea (KP - Restricted)' },
  { ref: 'CA', label: 'Canada (CA)' },
  { ref: 'DE', label: 'Germany (DE)' },
  { ref: 'FR', label: 'France (FR)' },
];

export const ChatView: React.FC<ChatViewProps> = ({
  messages,
  isBusy,
  onSendMessage,
  onConfirmAction,
  onCancelAction,
  onSelectCountry,
  onActionChipClick,
  onExploreProducts,
  tools,
  activeTool,
  onSupplyInputs,
  transcriptionProvider = defaultSpeechProvider,
}) => {
  const [inputText, setInputText] = useState('');
  const [selectedCountryCode, setSelectedCountryCode] = useState('AU');
  const [isRecording, setIsRecording] = useState(false);
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<Record<string, string>>({});

  // Initialize formValues with smart defaults whenever activeTool changes
  useEffect(() => {
    if (!activeTool) {
      setFormValues({});
      return;
    }
    const tool = tools.find((t) => t.name === activeTool);
    if (!tool) return;
    const initial: Record<string, string> = {};
    for (const f of tool.fields) {
      if (f.name === 'account_type') {
        initial[f.name] = 'transaction';
      } else if (f.name === 'issue_card' || f.name === 'additional_card' || f.type === 'boolean') {
        initial[f.name] = 'true';
      } else if (f.name === 'reason') {
        initial[f.name] = 'lost';
      } else if (f.options && f.options.length > 0) {
        initial[f.name] = f.options[0].ref;
      }
    }
    setFormValues(initial);
  }, [activeTool, tools]);

  const chatContainerRef = useRef<HTMLDivElement | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const baseTextRef = useRef<string>('');
  const isNearBottomRef = useRef<boolean>(true);

  // Monitor user scrolling to preserve position if reviewing past messages
  const handleScroll = () => {
    const el = chatContainerRef.current;
    if (!el) return;
    const threshold = 140; // px
    isNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
  };

  useEffect(() => {
    if (isNearBottomRef.current) {
      chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isBusy]);

  // Clean up speech recognition on unmount
  useEffect(() => {
    return () => {
      if (transcriptionProvider.isListening()) {
        transcriptionProvider.stop();
      }
    };
  }, [transcriptionProvider]);

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim() || isBusy) return;
    if (isRecording) {
      transcriptionProvider.stop();
      setIsRecording(false);
    }
    const textToSend = inputText.trim();
    setInputText('');
    await onSendMessage(textToSend);
  };

  const handleVoiceToggle = () => {
    if (!transcriptionProvider.isSupported()) {
      setVoiceNotice('Voice recognition is not supported on this browser. You can type your request.');
      return;
    }

    if (isRecording) {
      transcriptionProvider.stop();
      setIsRecording(false);
      setVoiceNotice(null);
    } else {
      baseTextRef.current = inputText;
      setVoiceNotice('Listening... Speak your request now.');
      setIsRecording(true);

      transcriptionProvider.start(
        (spokenTranscript) => {
          const base = baseTextRef.current.trim();
          const updated = base ? `${base} ${spokenTranscript}` : spokenTranscript;
          setInputText(updated);
        },
        (errorMsg) => {
          setVoiceNotice(errorMsg);
          setIsRecording(false);
        },
        () => {
          setIsRecording(false);
          setVoiceNotice(null);
        }
      );
    }
  };

  const handleCountrySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedCountryCode || isBusy) return;
    await onSelectCountry(selectedCountryCode);
  };

  const handleFormSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeTool || isBusy) return;
    const tool = tools.find((t) => t.name === activeTool);
    const finalVals: Record<string, string> = { ...formValues };
    if (tool) {
      for (const f of tool.fields) {
        if (finalVals[f.name] === undefined || finalVals[f.name] === '') {
          if (f.name === 'account_type') {
            finalVals[f.name] = 'transaction';
          } else if (f.name === 'issue_card' || f.name === 'additional_card' || f.type === 'boolean') {
            finalVals[f.name] = 'true';
          } else if (f.name === 'reason') {
            finalVals[f.name] = 'lost';
          } else if (f.options && f.options.length > 0 && f.required) {
            finalVals[f.name] = f.options[0].ref;
          }
        }
      }
    }
    await onSupplyInputs(activeTool, finalVals);
  };

  const latestMessage = messages[messages.length - 1];
  const currentActionTool = tools.find((t) => t.name === activeTool);

  return (
    <div className="chat-container">
      {/* Header */}
      <div className="chat-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div className="avatar assistant">💬</div>
          <div>
            <h3 style={{ fontSize: '1rem', fontWeight: 700, color: '#101828' }}>
              Neo Banking Assistant
            </h3>
            <div style={{ fontSize: '0.75rem', color: '#0d9488', display: 'flex', alignItems: 'center', gap: '4px' }}>
              <span className="pulse-dot" style={{ width: '6px', height: '6px' }}></span>
              Online &amp; Connected
            </div>
          </div>
        </div>
      </div>

      {/* Voice notice */}
      {voiceNotice && (
        <div
          style={{
            padding: '8px 16px',
            backgroundColor: isRecording ? '#eff6ff' : '#fef2f2',
            borderBottom: '1px solid #e2e8f0',
            fontSize: '0.85rem',
            color: isRecording ? '#1d4ed8' : '#b91c1c',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span>{isRecording ? '🎙️ ' : '⚠️ '}{voiceNotice}</span>
          {isRecording && (
            <button
              className="btn-secondary"
              style={{ padding: '2px 8px', fontSize: '0.75rem' }}
              onClick={handleVoiceToggle}
            >
              Stop
            </button>
          )}
        </div>
      )}

      {/* Message History Area */}
      <div className="chat-messages" ref={chatContainerRef} onScroll={handleScroll}>
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`message-bubble-wrapper ${msg.sender}`}
          >
            <div className={`avatar ${msg.sender}`}>
              {msg.sender === 'user' ? '👤' : '💬'}
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', maxWidth: '78%' }}>
              <div className={`message-bubble ${msg.sender}`}>
                {msg.text}

                {/* Country Selection Dropdown */}
                {msg.country_required && (
                  <div className="rich-action-card" style={{ marginTop: '12px' }}>
                    <div style={{ fontWeight: 600, color: '#101828', marginBottom: '8px', fontSize: '0.9rem' }}>
                      Select Replacement Country
                    </div>
                    <form onSubmit={handleCountrySubmit} style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                      <select
                        className="form-control"
                        style={{ flex: 1, minWidth: '180px' }}
                        value={selectedCountryCode}
                        onChange={(e) => setSelectedCountryCode(e.target.value)}
                        disabled={isBusy}
                        required
                      >
                        {COMMON_COUNTRIES.map((c) => (
                          <option key={c.ref} value={c.ref}>
                            {c.label}
                          </option>
                        ))}
                      </select>
                      <button type="submit" className="btn-primary" disabled={isBusy}>
                        Confirm Country
                      </button>
                    </form>
                  </div>
                )}

                {/* Human Confirmation Dialog */}
                {msg.can_confirm && msg.review && (
                  <div className="rich-action-card warning-box" style={{ marginTop: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#b45309', fontWeight: 700, fontSize: '0.9rem', marginBottom: '6px' }}>
                      <span>⚠️</span> Confirmation Required
                    </div>
                    <p style={{ fontSize: '0.85rem', color: '#1f2937', marginBottom: '14px', lineHeight: 1.4 }}>
                      Please review the details above and confirm to proceed with this operation.
                    </p>
                    <div style={{ display: 'flex', gap: '10px' }}>
                      {onCancelAction && (
                        <button
                          type="button"
                          className="btn-secondary"
                          style={{ flex: 1 }}
                          onClick={onCancelAction}
                          disabled={isBusy}
                        >
                          Cancel
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn-primary"
                        style={{ flex: 2 }}
                        onClick={onConfirmAction}
                        disabled={isBusy}
                      >
                        {msg.confirmation_label || 'Confirm Action'}
                      </button>
                    </div>
                  </div>
                )}

                {/* Product Offer Banner */}
                {msg.product_offer && (
                  <div className="product-explore-banner" style={{ marginTop: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '1.2rem' }}>✨</span>
                      <h3>{msg.product_offer.title}</h3>
                    </div>
                    <p>{msg.product_offer.description}</p>
                    <button
                      className="btn-primary"
                      style={{ backgroundColor: 'white', color: '#0f766e', alignSelf: 'flex-start' }}
                      onClick={onExploreProducts}
                    >
                      Explore Neo Bank Products →
                    </button>
                  </div>
                )}
              </div>
              <span className="message-timestamp">{msg.timestamp}</span>
            </div>
          </div>
        ))}

        {isBusy && (
          <div className="message-bubble-wrapper assistant">
            <div className="avatar assistant">💬</div>
            <div className="message-bubble assistant" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span className="pulse-dot"></span>
              <span style={{ color: '#64748b', fontSize: '0.9rem' }}>Checking with Neo Bank…</span>
            </div>
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      {/* Suggested Actions Bar */}
      {latestMessage?.suggested_actions && latestMessage.suggested_actions.length > 0 && (
        <div className="suggested-chips-bar">
          <span style={{ fontSize: '0.8rem', color: '#64748b', alignSelf: 'center', fontWeight: 600 }}>
            Suggestions:
          </span>
          {latestMessage.suggested_actions.map((action) => {
            const labelMap: Record<string, string> = {
              block_card: 'Block Card',
              block_and_replace: 'Block & Replace Card',
              replace_card: 'Request Replacement',
              select_country: 'Specify Country',
              create_account: 'Open Account',
              view_cards: 'View Cards',
              view_accounts: 'View Accounts',
            };
            return (
              <button
                key={action}
                type="button"
                className="action-chip"
                onClick={() => void onActionChipClick(action)}
                disabled={isBusy}
              >
                {labelMap[action] || action}
              </button>
            );
          })}
        </div>
      )}

      {/* Embedded Secure Tool Selection Form */}
      {currentActionTool && (
        <div style={{ padding: '16px 20px', background: '#f8fafc', borderTop: '1px solid #e2e8f0' }}>
          <h4 style={{ fontSize: '0.95rem', fontWeight: 700, color: '#101828', marginBottom: '6px' }}>
            {currentActionTool.label}: Selection Required
          </h4>
          <p style={{ fontSize: '0.8rem', color: '#64748b', marginBottom: '12px' }}>
            Please choose from your verified records below to proceed securely.
          </p>
          <form onSubmit={handleFormSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {currentActionTool.fields.map((field) => {
              // Predefined selection controls
              if (field.options && field.options.length > 0) {
                return (
                  <div key={field.name}>
                    <label style={{ fontSize: '0.8rem', fontWeight: 600, color: '#334155', display: 'block', marginBottom: '4px' }}>
                      {field.label}
                    </label>
                    <select
                      className="form-control"
                      required={field.required}
                      value={formValues[field.name] || ''}
                      onChange={(e) => setFormValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
                      disabled={isBusy}
                    >
                      <option value="">Select an option</option>
                      {field.options.map((opt) => (
                        <option key={opt.ref} value={opt.ref}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                );
              }

              if (field.name === 'account_type') {
                return (
                  <div key={field.name}>
                    <label style={{ fontSize: '0.8rem', fontWeight: 600, color: '#334155', display: 'block', marginBottom: '4px' }}>
                      Account Type
                    </label>
                    <select
                      className="form-control"
                      required
                      value={formValues[field.name] || 'transaction'}
                      onChange={(e) => setFormValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
                      disabled={isBusy}
                    >
                      <option value="transaction">Everyday Transaction</option>
                      <option value="savings">High-Interest Savings</option>
                    </select>
                  </div>
                );
              }

              if (field.name === 'reason') {
                return (
                  <div key={field.name}>
                    <label style={{ fontSize: '0.8rem', fontWeight: 600, color: '#334155', display: 'block', marginBottom: '4px' }}>
                      Reason
                    </label>
                    <select
                      className="form-control"
                      required
                      value={formValues[field.name] || 'lost'}
                      onChange={(e) => setFormValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
                      disabled={isBusy}
                    >
                      <option value="lost">Card Lost</option>
                      <option value="stolen">Card Stolen</option>
                      <option value="fraud">Suspected Fraud / Unauthorised Activity</option>
                      <option value="freeze">Temporary Security Freeze</option>
                      <option value="damaged">Card Damaged</option>
                    </select>
                  </div>
                );
              }

              if (field.name === 'issue_card' || field.name === 'additional_card' || field.type === 'boolean') {
                return (
                  <div key={field.name}>
                    <label style={{ fontSize: '0.8rem', fontWeight: 600, color: '#334155', display: 'block', marginBottom: '4px' }}>
                      {field.label || (field.name === 'issue_card' ? 'Issue Debit Card' : 'Additional Card')}
                    </label>
                    <select
                      className="form-control"
                      value={formValues[field.name] ?? 'true'}
                      onChange={(e) => setFormValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
                      disabled={isBusy}
                    >
                      <option value="true">Yes — Issue debit card with this account</option>
                      <option value="false">No — Account only (no card)</option>
                    </select>
                  </div>
                );
              }

              return (
                <div key={field.name}>
                  <label style={{ fontSize: '0.8rem', fontWeight: 600, color: '#334155', display: 'block', marginBottom: '4px' }}>
                    {field.label}
                  </label>
                  <input
                    type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'}
                    className="form-control"
                    required={field.required}
                    value={formValues[field.name] || ''}
                    onChange={(e) => setFormValues((prev) => ({ ...prev, [field.name]: e.target.value }))}
                    disabled={isBusy}
                  />
                </div>
              );
            })}
            <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
              <button type="submit" className="btn-primary" disabled={isBusy}>
                Proceed with Selection
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Input Bar */}
      <form className="chat-input-bar" onSubmit={handleSend}>
        <button
          type="button"
          className={`mic-btn ${isRecording ? 'recording' : ''}`}
          onClick={handleVoiceToggle}
          title={isRecording ? 'Stop recording' : 'Speak using microphone'}
          disabled={isBusy}
          aria-label={isRecording ? 'Stop recording' : 'Speak using microphone'}
        >
          {isRecording ? '🛑' : '🎙️'}
        </button>

        <textarea
          ref={textareaRef}
          className="chat-textarea"
          rows={1}
          placeholder="Ask Neo (e.g. 'Show my accounts' or 'I lost my card')..."
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSend(e);
            }
          }}
          disabled={isBusy}
          maxLength={4000}
        />

        <button
          type="submit"
          className="send-msg-btn"
          disabled={isBusy || !inputText.trim()}
          title="Send message"
          aria-label="Send message"
        >
          ➤
        </button>
      </form>
    </div>
  );
};
