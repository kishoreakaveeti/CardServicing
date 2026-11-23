## 1. Enterprise Agentic AI Reference Architecture

```mermaid
%%{init: {"flowchart": {"curve": "linear", "nodeSpacing": 55, "rankSpacing": 65}}}%%
flowchart TB
    Consumer["Customer / Employee / Enterprise System"] --> Experience["Digital Channel / Experience API"]

    subgraph Runtime["Agentic Application Runtime"]
        direction TB
        Entry["Agent API / Workflow Entry Point"] --> Privacy["Privacy Boundary<br/>PII Controls and Context Protection"]
        Privacy --> Orchestrator["Stateful Supervisor / Orchestrator"]
        Orchestrator --> Specialists["Pluggable Domain Agents"]
        Orchestrator -.-> State[("Workflow State and Checkpoints")]
    end

    subgraph Intelligence["Model and Knowledge Capabilities"]
        direction LR
        Models["Approved Model Access<br/>Hosted or Private Models"]
        Retrieval["Governed Knowledge Retrieval<br/>Approved Documents and Policies"]
    end

    subgraph Governance["Deterministic Governance"]
        direction LR
        Validate["Validate Proposals and Evidence"] --> Rules["Policy Decision Point<br/>Trusted Facts and Business Rules"]
        Rules --> Approval["Customer / Staff Approval<br/>When Required"]
    end

    subgraph Integration["Trusted Enterprise Integration"]
        direction LR
        Executor["Authorised Action Executor"] --> Gateway["Governed Tool Gateway / MCP"]
        Gateway --> DomainAPI["Domain APIs"] --> Records[("Systems of Record")]
    end

    subgraph Crosscutting["Enterprise Controls Across All Layers"]
        direction LR
        Identity["Identity and Delegated Access"]
        Audit["Observability / Audit / Security Monitoring"]
    end

    Experience --> Entry
    Specialists --> Validate
    Orchestrator -.-> Models
    Orchestrator -.-> Retrieval
    Approval --> Executor

    classDef runtime fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef control fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef trusted fill:#dcfce7,stroke:#16a34a,color:#14532d
    class Entry,Privacy,Orchestrator,Specialists runtime
    class Validate,Rules,Approval control
    class Executor,Gateway,DomainAPI trusted
```

## 2. CardServicing Deployment and Component Architecture

```mermaid
%%{init: {"flowchart": {"curve": "linear", "nodeSpacing": 55, "rankSpacing": 65}}}%%
flowchart TB
    Browser["Customer Browser"]
    Claude["Anthropic Claude<br/>External Model API"]
    Blob[("Azure Blob Storage<br/>Telemetry Sink")]

    subgraph Azure["Azure VM"]
        direction TB
        Proxy["Caddy<br/>Public HTTPS Reverse Proxy"]

        subgraph Docker["Docker Compose — Separate Containers"]
            direction TB
            Frontend["React Frontend / Nginx"] --> BFF["Experience BFF<br/>FastAPI HTTP Service"]
            BFF --> AgentAPI["Agent Orchestration API<br/>FastAPI: cards-agent"]
            AgentAPI --> FinAgent["FinCrime Agent API<br/>FastAPI: fincrime-agent"]
            AgentAPI --> MCP["Remote MCP Server<br/>Streamable HTTP"]
            MCP --> CardsAPI["Cards Banking API<br/>FastAPI: cards-api"]
            MCP --> FinAPI["FinCrime Servicing API<br/>FastAPI: fincrime-api"]
        end

        subgraph Platform["Supporting Containers and Persistent Data"]
            direction LR
            Qdrant[("Qdrant<br/>Policy Vector Index")]
            OPA["OPA<br/>Policy Publication Sync"]
            Keycloak["Keycloak / OIDC"]
            MariaDB[("MariaDB<br/>Identity Database")]
            SQLite[("Service-Owned SQLite<br/>Persistent Volumes")]
        end
    end

    Browser --> Proxy --> Frontend
    BFF -.->|"Direct Account / Card Routes"| CardsAPI
    AgentAPI -->|"Policy Retrieval"| Qdrant
    AgentAPI -.->|"Publication Only"| OPA
    BFF -.-> Keycloak
    Keycloak --> MariaDB
    AgentAPI -.-> SQLite
    AgentAPI --> Claude
    FinAgent --> Claude
    AgentAPI -.-> Blob

    classDef service fill:#dbeafe,stroke:#2563eb,color:#1e40af
    classDef storage fill:#dcfce7,stroke:#16a34a,color:#14532d
    class BFF,AgentAPI,FinAgent,MCP,CardsAPI,FinAPI service
    class Qdrant,MariaDB,SQLite,Blob storage
```

## 3. CardServicing LangGraph Multi-Agent Orchestration

```mermaid
%%{init: {"flowchart": {"curve": "linear", "nodeSpacing": 60, "rankSpacing": 70}}}%%
flowchart TB
    BFF["Experience BFF"]
    Remote["FinCrime Agent API<br/>Separate HTTP Service"]
    Claude["Anthropic Claude API"]
    Qdrant[("Qdrant Policy Index")]

    subgraph Host["cards-agent — FastAPI Container"]
        direction TB
        HTTP["Agent API HTTP Handler"] --> Presidio["Microsoft Presidio<br/>Sanitise and Tokenise Input"]
        Presidio --> Context["Sanitised Request<br/>Verified Facts and Allowed Tools"]
        Context --> Runner["AgentOrchestrator<br/>Invoke LangGraph"]

        subgraph Graph["LangGraph StateGraph — Internal Python Nodes"]
            direction TB
            Supervisor["Supervisor Agent<br/>Graph Entry and Handoff Coordination"]
            Cards["Cards Specialist<br/>Local Agent Node"]
            FinNode["FinCrime Specialist<br/>Local Agent Node"]
            Search["policy_search<br/>Retrieval Node"]
            Plan["policy_plan<br/>Grounded Planning Node"]
            Confirm["human_confirmation<br/>Interrupt / Resume"]
            Staff["fincrime_approval<br/>Staff Review Interrupt"]

            Supervisor -->|"Cards Handoff"| Cards
            Supervisor -->|"FinCrime Handoff"| FinNode
            Supervisor -->|"Policy Journey"| Search
            Search --> Plan
            Supervisor --> Confirm
            Supervisor --> Staff
        end

        Checkpoints[("Durable LangGraph<br/>SQLite Checkpoints")]
        Runner --> Supervisor
        Runner -.-> Checkpoints
    end

    BFF --> HTTP
    FinNode -->|"HTTP /turn"| Remote
    Search -->|"Retrieve Evidence"| Qdrant
    Supervisor -.->|"Model Reasoning as Needed"| Claude
    Remote -.->|"Specialist Inference"| Claude

    classDef agent fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef graphNode fill:#fef3c7,stroke:#d97706,color:#78350f
    class Supervisor,Cards,FinNode agent
    class Search,Plan,Confirm,Staff graphNode
```

## 4. CardServicing RAG, Policy Governance and Trusted Execution

```mermaid
%%{init: {"flowchart": {"curve": "linear", "nodeSpacing": 55, "rankSpacing": 65}}}%%
flowchart TB
    Intent["Sanitised Intent and Verified Domain Facts"] --> Agent["Supervisor / Domain Specialist"]

    subgraph Reasoning["Reasoning and Evidence — Advisory"]
        direction TB
        Retrieve["Hybrid Policy Retriever<br/>Mandatory and Semantic Search"]
        Qdrant[("Qdrant<br/>Approved Policy Index")]
        Evidence["Bounded Policy Evidence<br/>Versioned and Redacted"]
        Model["Claude<br/>Grounded Plan / Tool Proposal"]
        Retrieve --> Qdrant
        Retrieve --> Evidence --> Model
    end

    subgraph Controls["Deterministic Controls — Authoritative"]
        direction TB
        Validate["Validate Proposal and Policy Evidence"] --> Evaluate["Python Policy Evaluator<br/>Geographic Rules and Trusted Facts"]
        Evaluate --> Decision{"Authorised and Eligible?"}
        Decision -->|"Denied / Unavailable"| Safe["Clarify / Reject / Escalate"]
    end

    subgraph Execution["Trusted Execution"]
        direction TB
        Approval["Customer Confirmation<br/>Staff Approval If Required"] --> Executor["FastAPI Trusted Executor<br/>Revalidate / Authorise / Idempotency"]
        Executor --> MCP["MCP Client and Remote MCP Server"]
        MCP --> Banking["Cards / FinCrime Banking APIs"]
        Banking --> Result["Persist Outcome and Resume Workflow"]
    end

    subgraph Publication["Separate Administrative Policy Publication"]
        direction LR
        Admin["Policy Administrator"] --> Release["Versioned Policy Release<br/>Reindex Qdrant and Sync Rules"]
        Release --> OPA["OPA Service<br/>Not Used for Live Decisions"]
    end

    Agent -->|"Policy-Sensitive Journey"| Retrieve
    Agent -.->|"Other Eligible Tool Proposals"| Validate
    Model --> Validate
    Decision -->|"Permitted"| Approval

    classDef advisory fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
    classDef control fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef trusted fill:#dcfce7,stroke:#16a34a,color:#14532d
    class Retrieve,Evidence,Model advisory
    class Validate,Evaluate,Decision control
    class Approval,Executor,MCP,Banking trusted
```
