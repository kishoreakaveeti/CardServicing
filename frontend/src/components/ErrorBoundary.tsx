import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
}

interface State {
  hasError: boolean;
  errorMessage?: string;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
  };

  public static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      errorMessage: error?.message || 'An unexpected runtime error occurred.',
    };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught an unhandled error:', error, errorInfo);
  }

  private handleReset = () => {
    this.setState({ hasError: false, errorMessage: undefined });
    window.location.reload();
  };

  private handleGoHome = () => {
    const isFinCrime = window.location.pathname.startsWith('/fincrime');
    window.location.href = isFinCrime ? '/fincrime' : '/';
  };

  public render() {
    if (this.state.hasError) {
      const isFinCrime = window.location.pathname.startsWith('/fincrime');
      return (
        <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: isFinCrime ? '#0b0f19' : '#f8fafc',
            color: isFinCrime ? '#f8fafc' : '#0f172a',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
            padding: '24px',
          }}
        >
          <div
            style={{
              maxWidth: '480px',
              width: '100%',
              backgroundColor: isFinCrime ? '#0f172a' : '#ffffff',
              border: `1px solid ${isFinCrime ? '#1e293b' : '#e2e8f0'}`,
              borderRadius: '16px',
              padding: '32px',
              boxShadow: '0 10px 25px -5px rgba(0,0,0,0.1)',
              textAlign: 'center',
            }}
          >
            <div style={{ fontSize: '2.5rem', marginBottom: '16px' }}>🛡️</div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: 700, marginBottom: '8px' }}>
              {this.props.fallbackTitle || (isFinCrime ? 'FinCrime Portal Notice' : 'Service Notice')}
            </h2>
            <p
              style={{
                fontSize: '0.9rem',
                color: isFinCrime ? '#94a3b8' : '#64748b',
                lineHeight: 1.5,
                marginBottom: '24px',
              }}
            >
              An unexpected display issue occurred. Your data and session remain secure. Please refresh to continue.
            </p>
            <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
              <button
                onClick={this.handleReset}
                style={{
                  padding: '10px 18px',
                  backgroundColor: '#0284c7',
                  color: '#ffffff',
                  border: 'none',
                  borderRadius: '8px',
                  fontWeight: 600,
                  fontSize: '0.88rem',
                  cursor: 'pointer',
                }}
              >
                Reload View
              </button>
              <button
                onClick={this.handleGoHome}
                style={{
                  padding: '10px 18px',
                  backgroundColor: 'transparent',
                  color: isFinCrime ? '#cbd5e1' : '#475569',
                  border: `1px solid ${isFinCrime ? '#334155' : '#cbd5e1'}`,
                  borderRadius: '8px',
                  fontWeight: 600,
                  fontSize: '0.88rem',
                  cursor: 'pointer',
                }}
              >
                Go to Portal Home
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
