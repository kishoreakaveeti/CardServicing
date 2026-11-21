### End To End Architecture
![Architecture Diagram](./Diagrams/E2E_Architecture.svg)

### Agentic AI Pattern
![Architecture Diagram](./Diagrams/AgenticAI_Pattern.svg)

### Agent Orchestration
![Architecture Diagram](./Diagrams/AgenticOrchestration_LangGraph.svg)

## Preserved System Runtime and Deployment Specifications

The following specifications detail the existing runtime environment, multi-agent sequence mechanics, persistence models, and deployment topologies.

### Runtime Boundaries

```mermaid
flowchart LR
    U[React chat and secure forms] --> B[Experience BFF]
    B --> A[Agent Orchestrator and durable jobs]
    A --> L[Provider: stub or Anthropic]
    A --> P[Approved policy retrieval and deterministic guards]
    P --> Q[(Qdrant policy index)]
    P -->|Redacted evidence and verified boolean facts| L
    L -->|Cited next-step proposal| P
    A --> M[Remote HTTP MCP]
    M --> C[Cards banking API]
    M --> FC[FinCrime servicing API]
    B -->|Credential services and owned-account refresh| C
    C --> S[(SQLite banking records)]
    FC --> FS[(SQLite FinCrime records)]
    FC -->|Sync card status| C
    B --> I[Keycloak Identity]
    A --> I
    M --> I
    C --> I
    FC --> I
    B --> D[(Mongo diagnostics)]
    A --> D
    M --> D
    C --> D
    FC --> D
```



---