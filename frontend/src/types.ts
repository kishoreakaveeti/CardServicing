export type Session = {
  name: string;
  csrf: string;
};

export type AccountRecord = {
  account_number: string;
  account_type: 'transaction' | 'savings' | string;
  created_at: string;
  status?: string;
};

export type CardRecord = {
  id: string;
  card_number: string;
  account_number: string;
  status: 'created' | 'blocked' | string;
  user_name?: string;
  given_name?: string;
  created_at?: string;
};

export type TraceTelemetry = {
  e2e_latency_ms: number;
  llm_latency_ms: number;
  rag_latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: string;
};

export type TraceInfo = {
  customer_request: string;
  intent: {
    primary: string;
    secondary: string[];
  };
  country_inference: {
    declared_location: string;
    country: string;
    replacement_country_code: string;
  };
  sentiment: string;
  urgency: string;
  retrieved_policies: {
    id: string;
    title: string;
    version: string;
  }[];
  policy_evaluation: {
    geographic_restriction: string;
    replacement_permitted: boolean;
    block_permitted: boolean;
    promotions_permitted: boolean;
  };
  allowed_capabilities: string[];
  denied_capabilities: string[];
  selected_agent: string;
  selected_tool: string;
  human_approval: {
    required: boolean;
    status: string;
  };
  backend_outcome: string;
  conversation_summary: string;
  telemetry: TraceTelemetry;
};

export type ProductOffer = {
  title: string;
  description: string;
  action: string;
  link?: string;
};

export type Reply = {
  id?: string;
  message: string;
  review: string | null;
  can_confirm: boolean;
  confirmation_label: string | null;
  input_tool?: string | null;
  country_required?: boolean;
  trace?: TraceInfo | null;
  suggested_actions?: string[];
  product_offer?: ProductOffer | null;
  sentiment?: string | null;
  urgency?: string | null;
  data?: {
    accounts?: AccountRecord[];
    cards?: CardRecord[];
  };
};

export type ChatMessage = {
  id: string;
  sender: 'user' | 'assistant';
  text: string;
  timestamp: string;
  trace?: TraceInfo | null;
  suggested_actions?: string[];
  product_offer?: ProductOffer | null;
  can_confirm?: boolean;
  review?: string | null;
  confirmation_label?: string | null;
  country_required?: boolean;
  input_tool?: string | null;
};

export type Job = {
  status: 'pending' | 'running' | 'succeeded' | 'failed';
  status_url: string;
  result: Reply | null;
  error: string | null;
};

export type PendingAction = {
  path: string;
  body: object;
  key: string;
};

export type SelectionOption = {
  ref: string;
  label: string;
};

export type InputField = {
  name: string;
  label: string;
  type: 'string' | 'integer' | 'number' | 'boolean';
  required: boolean;
  options?: SelectionOption[];
};

export type InputTool = {
  name: string;
  label: string;
  fields: InputField[];
};

export type ActivityItem = {
  id: string;
  title: string;
  timestamp: string;
  type: 'security' | 'card' | 'account' | 'auth';
  details: string;
};
