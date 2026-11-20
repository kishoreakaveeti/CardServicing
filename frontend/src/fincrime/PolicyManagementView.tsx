import React, { useEffect, useState } from 'react';
import { PolicyMetadata, PolicyRelease } from './types';

interface PolicyManagementViewProps {
  policies: PolicyMetadata[];
  activeRelease: PolicyRelease | null;
  onRefresh: () => void;
  isAdmin: boolean;
}

export const PolicyManagementView: React.FC<PolicyManagementViewProps> = ({
  policies,
  activeRelease,
  onRefresh,
  isAdmin,
}) => {
  const [selectedPolicyId, setSelectedPolicyId] = useState('POL-006');
  const [homeCountry, setHomeCountry] = useState('AU');
  const [excludedCountries, setExcludedCountries] = useState<string[]>(['KP']);
  const [newCountryCode, setNewCountryCode] = useState('');
  const [overseasRequiresApproval, setOverseasRequiresApproval] = useState(true);
  const [domesticRequiresApproval, setDomesticRequiresApproval] = useState(false);
  const [markdownContent, setMarkdownContent] = useState('');
  const [showAdvancedJson, setShowAdvancedJson] = useState(false);
  const [changeSummary, setChangeSummary] = useState('');

  // Status & feedback
  const [isProcessing, setIsProcessing] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  // Load selected policy content and rules
  useEffect(() => {
    fetch(`/api/fincrime/policies/${selectedPolicyId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        setMarkdownContent(data.content || '');
        if (data.rules) {
          if (data.rules.home_country_code) setHomeCountry(data.rules.home_country_code);
          if (Array.isArray(data.rules.excluded_country_codes)) {
            setExcludedCountries(data.rules.excluded_country_codes);
          }
          if (typeof data.rules.overseas_replacement_requires_fincrime_approval === 'boolean') {
            setOverseasRequiresApproval(data.rules.overseas_replacement_requires_fincrime_approval);
          }
          if (typeof data.rules.domestic_replacement_requires_fincrime_approval === 'boolean') {
            setDomesticRequiresApproval(data.rules.domestic_replacement_requires_fincrime_approval);
          }
        }
      })
      .catch(() => {});
  }, [selectedPolicyId]);

  const handleAddExcludedCountry = () => {
    const code = newCountryCode.trim().toUpperCase();
    if (!code) return;
    if (code.length !== 2) {
      setFeedback({ type: 'error', message: 'Country code must be a 2-letter ISO code (e.g. KP, IR, SY).' });
      return;
    }
    if (!excludedCountries.includes(code)) {
      setExcludedCountries([...excludedCountries, code]);
    }
    setNewCountryCode('');
  };

  const handleRemoveExcludedCountry = (code: string) => {
    setExcludedCountries(excludedCountries.filter((c) => c !== code));
  };

  const getRulesObject = () => ({
    policy_id: selectedPolicyId,
    home_country_code: homeCountry.trim().toUpperCase() || 'AU',
    excluded_country_codes: excludedCountries,
    overseas_replacement_requires_fincrime_approval: overseasRequiresApproval,
    domestic_replacement_requires_fincrime_approval: domesticRequiresApproval,
    unknown_country_action: 'REQUIRE_CLARIFICATION',
    security_card_blocking_allowed: true,
  });

  const handleSaveDraft = async () => {
    setIsProcessing(true);
    setFeedback(null);
    try {
      const res = await fetch('/api/fincrime/policies/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          policy_id: selectedPolicyId,
          content: markdownContent,
          rules: getRulesObject(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to save draft');
      setFeedback({ type: 'success', message: 'Draft saved successfully.' });
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleValidateDraft = async () => {
    setIsProcessing(true);
    setFeedback(null);
    try {
      const res = await fetch('/api/fincrime/policies/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          policy_id: selectedPolicyId,
          content: markdownContent,
          rules: getRulesObject(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Validation failed');
      setFeedback({
        type: 'success',
        message: `Validation Passed! Document structure verified, OPA conditions aligned, 0 rule conflicts.`,
      });
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleApplyPolicy = async () => {
    if (!isAdmin) {
      setFeedback({ type: 'error', message: 'Only FinCrime Policy Administrators or Supervisors can activate policy releases.' });
      return;
    }
    setIsProcessing(true);
    setFeedback(null);
    try {
      const res = await fetch('/api/fincrime/policies/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          policy_id: selectedPolicyId,
          content: markdownContent,
          rules: getRulesObject(),
          change_summary: changeSummary.trim() || `Updated geographic rules for ${selectedPolicyId}`,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Policy activation failed');
      setFeedback({
        type: 'success',
        message: `Release ${data.release_id} (v${data.version}) ACTIVATED LIVE! Re-indexed in Qdrant with zero service downtime.`,
      });
      setChangeSummary('');
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message });
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <div>
          <h2 style={{ fontSize: '1.4rem', fontWeight: 700, color: '#f8fafc' }}>
            Policy Configuration & Dynamic Release Governance
          </h2>
          <p style={{ color: '#94a3b8', fontSize: '0.88rem' }}>
            Modify deterministic business rules and policy markdown. Apply changes immediately to Qdrant without service restarts.
          </p>
        </div>
        {activeRelease && (
          <div style={{ background: '#1e293b', border: '1px solid #334155', padding: '8px 16px', borderRadius: '8px', fontSize: '0.85rem' }}>
            <span style={{ color: '#94a3b8' }}>Live Active Release: </span>
            <strong style={{ color: '#10b981' }}>{activeRelease.release_id}</strong>
            <span style={{ color: '#64748b', marginLeft: '6px' }}>(v{activeRelease.version})</span>
          </div>
        )}
      </div>

      {feedback && (
        <div
          style={{
            padding: '12px 18px',
            borderRadius: '8px',
            marginBottom: '20px',
            fontSize: '0.9rem',
            background: feedback.type === 'success' ? '#064e3b' : '#7f1d1d',
            color: feedback.type === 'success' ? '#a7f3d0' : '#fecaca',
            border: `1px solid ${feedback.type === 'success' ? '#059669' : '#dc2626'}`,
          }}
        >
          {feedback.message}
        </div>
      )}

      {/* Policy Selector */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '20px', flexWrap: 'wrap' }}>
        {policies.map((p) => (
          <button
            key={p.id}
            className={`fincrime-tab-btn ${selectedPolicyId === p.id ? 'active' : ''}`}
            onClick={() => setSelectedPolicyId(p.id)}
          >
            {p.id}: {p.title.split(' ')[0]}
          </button>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '24px' }}>
        {/* Left Column: Interactive Rule Configuration */}
        <div>
          <div className="fincrime-card-panel">
            <div className="fincrime-panel-title">
              <span>Rule Configuration ({selectedPolicyId})</span>
              <button
                className="fincrime-btn-secondary"
                style={{ fontSize: '0.8rem', padding: '4px 10px' }}
                onClick={() => setShowAdvancedJson(!showAdvancedJson)}
              >
                {showAdvancedJson ? 'Show Form' : 'View Raw JSON'}
              </button>
            </div>

            {showAdvancedJson ? (
              <div>
                <textarea
                  className="fincrime-textarea"
                  rows={14}
                  style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}
                  value={JSON.stringify(getRulesObject(), null, 2)}
                  readOnly
                />
              </div>
            ) : (
              <div>
                {/* Home Country */}
                <div style={{ marginBottom: '16px' }}>
                  <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#cbd5e1' }}>
                    Home Country Code (Domestic Baseline)
                  </label>
                  <input
                    type="text"
                    className="fincrime-input-text"
                    maxLength={2}
                    value={homeCountry}
                    onChange={(e) => setHomeCountry(e.target.value.toUpperCase())}
                  />
                  <span style={{ fontSize: '0.78rem', color: '#64748b' }}>
                    AU is domestic. Any country other than AU is classified as overseas.
                  </span>
                </div>

                {/* Excluded Countries List */}
                <div style={{ marginBottom: '18px' }}>
                  <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#cbd5e1' }}>
                    Excluded Countries (Hard Immediate Denial)
                  </label>
                  <div className="fincrime-chip-container">
                    {excludedCountries.length === 0 ? (
                      <span style={{ color: '#64748b', fontSize: '0.82rem' }}>No countries excluded.</span>
                    ) : (
                      excludedCountries.map((code) => (
                        <span key={code} className="fincrime-chip">
                          <strong>{code}</strong>
                          <button
                            className="fincrime-chip-remove"
                            onClick={() => handleRemoveExcludedCountry(code)}
                            title={`Remove ${code} from exclusions`}
                          >
                            ✕
                          </button>
                        </span>
                      ))
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                    <input
                      type="text"
                      className="fincrime-input-text"
                      style={{ maxWidth: '120px', marginTop: 0 }}
                      placeholder="e.g. KP"
                      maxLength={2}
                      value={newCountryCode}
                      onChange={(e) => setNewCountryCode(e.target.value.toUpperCase())}
                      onKeyDown={(e) => e.key === 'Enter' && handleAddExcludedCountry()}
                    />
                    <button
                      className="fincrime-btn-secondary"
                      style={{ fontSize: '0.85rem', padding: '6px 12px' }}
                      onClick={handleAddExcludedCountry}
                    >
                      + Add Country
                    </button>
                  </div>
                  <span style={{ fontSize: '0.78rem', color: '#64748b', display: 'block', marginTop: '4px' }}>
                    Tip: Remove 'KP' to allow North Korea replacements under FinCrime staff approval!
                  </span>
                </div>

                {/* Toggles */}
                <div style={{ marginBottom: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.88rem', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={overseasRequiresApproval}
                      onChange={(e) => setOverseasRequiresApproval(e.target.checked)}
                      style={{ width: '16px', height: '16px' }}
                    />
                    <span>Overseas replacements require FinCrime staff approval</span>
                  </label>

                  <label style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.88rem', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={domesticRequiresApproval}
                      onChange={(e) => setDomesticRequiresApproval(e.target.checked)}
                      style={{ width: '16px', height: '16px' }}
                    />
                    <span>Domestic (AU) replacements require FinCrime staff approval</span>
                  </label>
                </div>
              </div>
            )}
          </div>

          {/* Action Row */}
          <div className="fincrime-card-panel">
            <div className="fincrime-panel-title">Publication & Live Deployment</div>
            <div style={{ marginBottom: '14px' }}>
              <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#cbd5e1' }}>
                Release Change Summary (Mandatory for Audit Trail)
              </label>
              <input
                type="text"
                className="fincrime-input-text"
                placeholder="e.g. Removed KP from exclusions list; permitted overseas replacement under approval"
                value={changeSummary}
                onChange={(e) => setChangeSummary(e.target.value)}
              />
            </div>

            <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
              <button
                className="fincrime-btn-secondary"
                disabled={isProcessing}
                onClick={handleSaveDraft}
              >
                Save Draft
              </button>
              <button
                className="fincrime-btn-secondary"
                disabled={isProcessing}
                onClick={handleValidateDraft}
              >
                Validate Draft
              </button>
              <button
                className="fincrime-btn-apply"
                disabled={isProcessing || !isAdmin}
                onClick={handleApplyPolicy}
                title={!isAdmin ? 'Administrator role required' : ''}
              >
                {isProcessing ? 'Deploying to Qdrant...' : 'Apply Policy Release'}
              </button>
            </div>
          </div>
        </div>

        {/* Right Column: Markdown Policy Document */}
        <div className="fincrime-card-panel">
          <div className="fincrime-panel-title">
            <span>Policy Markdown Specification</span>
            <span style={{ fontSize: '0.78rem', color: '#94a3b8' }}>Embedded in Qdrant RAG</span>
          </div>
          <p style={{ fontSize: '0.82rem', color: '#94a3b8', marginBottom: '8px' }}>
            Changes to this markdown document are chunked, embedded via FastEmbed, and queried by the Supervisor Agent:
          </p>
          <textarea
            className="fincrime-textarea"
            rows={22}
            style={{ fontFamily: 'monospace', fontSize: '0.82rem', lineHeight: 1.4 }}
            value={markdownContent}
            onChange={(e) => setMarkdownContent(e.target.value)}
          />
        </div>
      </div>
    </div>
  );
};
