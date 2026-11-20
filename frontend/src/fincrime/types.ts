export interface StaffSession {
  authenticated: boolean;
  user_name: string;
  roles: string[];
  csrf: string;
  is_staff: boolean;
  is_admin: boolean;
  is_analyst: boolean;
}

export interface FinCrimeCase {
  case_id: string;
  customer_id: string;
  card_id: string;
  conversation_id: string;
  action: string;
  current_country: string;
  home_country: string;
  status: 'PENDING' | 'APPROVED' | 'APPROVED_PENDING_EXECUTION' | 'PROCESSING' | 'REJECTED' | 'COMPLETED' | 'FAILED' | 'REVALIDATION_REQUIRED';
  risk_score: number;
  risk_level: string;
  reasons: string[];
  policy_eval?: {
    permitted?: boolean;
    requires_human_approval?: boolean;
    policy_id?: string;
    reason?: string;
    geographic_restriction?: string;
  };
  created_at: string;
  updated_at: string;
  decided_by?: string | null;
  decision_reason?: string | null;
}

export interface PolicyMetadata {
  id?: string;
  policy_id?: string;
  title?: string;
  category?: string;
  description?: string;
  version?: string;
  current_version?: string;
  active_version?: string;
  effective_date?: string;
  rules_summary?: {
    home_country_code?: string;
    excluded_country_codes?: string[];
    overseas_replacement_requires_fincrime_approval?: boolean;
    domestic_replacement_requires_fincrime_approval?: boolean;
  };
  conditions?: Record<string, any>;
  content?: string;
}

export interface PolicyRelease {
  release_id: string;
  release_number: number;
  version: string;
  status: string;
  activated_at: string;
  activated_by: string;
  change_summary: string;
  policy_id: string;
  rules_snapshot?: Record<string, any>;
}

export interface AuditEntry {
  event_id: string;
  event_type: string;
  actor: string;
  actor_role: string;
  timestamp: string;
  details?: Record<string, any>;
}

export interface QdrantStatus {
  status: string;
  connected: boolean;
  collection_name: string;
  vectors_count: number;
  points_count: number;
  active_release?: string;
  sync_status: string;
}
