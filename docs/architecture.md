# CampusFlow AI — Hackathon Architecture

> **Pitch:** One campus copilot for services, policy and safety — with an AI brain for understanding and a deterministic control plane for trust.

CampusFlow does not give an LLM direct access to institutional actions. Gemini may understand, decompose and extract; every action must still cross policy, identity, permission, risk, evidence and confirmation gates. The result is an agent that acts quickly on low-risk work while escalating sensitive decisions to accountable humans.

## 1. System architecture — the Trust Gateway

![CampusFlow AI Trust Gateway architecture](images/campusflow-trust-gateway.png)

_Presentation asset: [download the full-resolution PNG](images/campusflow-trust-gateway.png). The editable Mermaid version follows._

```mermaid
flowchart LR
    U["👤 Student · Faculty · Staff · Admin"]

    subgraph EXPERIENCE["EXPERIENCE LAYER"]
        direction TB
        UI["⚡ React Campus Copilot<br/>Chat · Voice input · Streaming UI"]
        PORTAL["📋 Role-aware portals<br/>Requests · Approvals · Maintenance"]
        MULTI["📷 Multimodal evidence<br/>IDs · Marksheets · Fault photos"]
    end

    subgraph API["IDENTITY + AGENT API"]
        direction TB
        AUTH["🔐 JWT identity<br/>Unique email + roll number"]
        MEMORY["🧠 Conversation memory<br/>Durable threads + workflow state"]
        ROUTER["🧭 Authoritative turn router<br/>Safety precedence · Multi-task split"]
        GEMINI["✨ Gemini proposal layer<br/>Intent · entities · planning · vision"]
    end

    subgraph TRUST["CAMPUSFLOW TRUST GATEWAY"]
        direction TB
        RAG["📚 Grounded policy retrieval<br/>Policy ID · version · citation · confidence"]
        GUARD["🛡️ Policy Guardian"]
        RBAC["🪪 RBAC permission check"]
        RISK["⚖️ Deterministic risk engine"]
        AUTO{"🚦 Autonomy decision"}
        GUARD --> RBAC --> RISK --> AUTO
        RAG --> GUARD
    end

    subgraph ACTIONS["CONTROLLED ACTION PLANE"]
        ACT["✅ ACT<br/>Create low-risk ticket"]
        ASK["💬 ASK<br/>Collect details / confirm"]
        APPROVE["👩‍💼 APPROVE / ESCALATE<br/>Authorized human review"]
        STOP["⛔ STOP<br/>Block unsafe/conflicting action"]
    end

    subgraph RECORDS["EVIDENCE + ACCOUNTABILITY"]
        DB[("SQLite<br/>Users · conversations · knowledge gaps")]
        DOCS[("Versioned Markdown policy corpus")]
        FILES[("Protected evidence storage")]
        AUDIT[("Tamper-evident audit chain")]
        NOTIFY["🔔 User notifications + status"]
    end

    U --> UI
    U --> PORTAL
    UI --> AUTH
    PORTAL --> AUTH
    MULTI --> AUTH
    AUTH --> MEMORY --> ROUTER
    ROUTER -->|action proposal| GUARD
    ROUTER -->|policy question| RAG
    ROUTER -.->|ambiguous / compound| GEMINI
    GEMINI -.->|schema-validated proposal only| GUARD
    DOCS --> RAG
    AUTO -->|low risk + allowed| ACT
    AUTO -->|missing data / confirmation| ASK
    AUTO -->|sensitive decision| APPROVE
    AUTO -->|policy conflict / denied| STOP
    ACT --> AUDIT
    ASK --> AUDIT
    APPROVE --> AUDIT
    STOP --> AUDIT
    ACT --> NOTIFY
    APPROVE --> NOTIFY
    MEMORY --> DB
    MULTI --> FILES

    classDef human fill:#172554,stroke:#60a5fa,color:#eff6ff,stroke-width:2px;
    classDef ai fill:#3b0764,stroke:#c084fc,color:#faf5ff,stroke-width:2px;
    classDef trust fill:#042f2e,stroke:#2dd4bf,color:#f0fdfa,stroke-width:2px;
    classDef action fill:#422006,stroke:#fbbf24,color:#fffbeb,stroke-width:2px;
    classDef data fill:#111827,stroke:#64748b,color:#f8fafc;
    class U,UI,PORTAL,MULTI human;
    class ROUTER,GEMINI,MEMORY ai;
    class AUTH,RAG,GUARD,RBAC,RISK,AUTO trust;
    class ACT,ASK,APPROVE,STOP action;
    class DB,DOCS,FILES,AUDIT,NOTIFY data;
```

### The design principle

```text
Gemini can PROPOSE  →  Trust Gateway must DECIDE  →  Controlled services may EXECUTE
```

This boundary is the main differentiator. Prompt injection, hallucination or a model error cannot directly bypass role permissions or invoke a protected action.

## 2. End-to-end agent flow — understand, govern, act, prove

![CampusFlow governed agent execution flow](images/campusflow-agent-flow.png)

_Presentation asset: [download the full-resolution PNG](images/campusflow-agent-flow.png). The editable Mermaid version follows._

```mermaid
flowchart TD
    START(["User sends text, voice or image-assisted request"])
    ID["Authenticate identity and authoritative role"]
    TURN["Load conversation + classify latest turn"]
    SAFE{"Safety-critical language?"}
    MULTI{"Multiple independent tasks?"}
    PLAN["Decompose tasks in user order"]
    UNDERSTAND["Extract intent + bounded entities"]
    INFO{"Policy question?"}
    RETRIEVE["Retrieve official policy document"]
    GROUNDED{"Relevant fact + citation available?"}
    GAP["Create deduplicated knowledge-gap request<br/>Tell user we do not know — never guess"]
    ANSWER["Answer with policy name, version and source"]
    COMPLETE{"Required details complete?"}
    FOLLOWUP["Ask only for missing fields<br/>Remember collected details"]
    POLICY["Validate policy relevance and conflicts"]
    PERMISSION["Check role permission"]
    RISK["Assess deterministic risk"]
    DECIDE{"ACT · ASK · APPROVE · STOP"}
    EXECUTE["Execute controlled low-risk action"]
    CONFIRM["Request explicit confirmation"]
    HUMAN["Route evidence to authorized human"]
    BLOCK["Stop action and explain safe alternative"]
    RECORD["Create audit event + request status"]
    NOTICE["Notify requester and expose live status"]
    MORE{"More planned tasks?"}
    DONE(["Ordered task results returned in one turn"])

    START --> ID --> TURN --> SAFE
    SAFE -->|yes| HUMAN
    SAFE -->|no| MULTI
    MULTI -->|yes| PLAN --> UNDERSTAND
    MULTI -->|no| UNDERSTAND
    UNDERSTAND --> INFO
    INFO -->|yes| RETRIEVE --> GROUNDED
    GROUNDED -->|no| GAP --> MORE
    GROUNDED -->|yes| ANSWER --> MORE
    INFO -->|no| COMPLETE
    COMPLETE -->|no| FOLLOWUP --> DONE
    COMPLETE -->|yes| POLICY --> PERMISSION --> RISK --> DECIDE
    DECIDE -->|ACT: low risk| EXECUTE --> RECORD
    DECIDE -->|ASK| CONFIRM --> RECORD
    DECIDE -->|APPROVE / ESCALATE| HUMAN --> RECORD
    DECIDE -->|STOP| BLOCK --> RECORD
    RECORD --> NOTICE --> MORE
    MORE -->|yes| UNDERSTAND
    MORE -->|no| DONE

    classDef start fill:#172554,stroke:#60a5fa,color:#eff6ff,stroke-width:2px;
    classDef intelligence fill:#3b0764,stroke:#c084fc,color:#faf5ff;
    classDef trust fill:#042f2e,stroke:#2dd4bf,color:#f0fdfa;
    classDef action fill:#422006,stroke:#fbbf24,color:#fffbeb;
    classDef stop fill:#450a0a,stroke:#f87171,color:#fff1f2;
    class START,DONE start;
    class TURN,MULTI,PLAN,UNDERSTAND intelligence;
    class ID,SAFE,INFO,RETRIEVE,GROUNDED,COMPLETE,POLICY,PERMISSION,RISK,DECIDE trust;
    class ANSWER,FOLLOWUP,EXECUTE,CONFIRM,HUMAN,RECORD,NOTICE,MORE action;
    class GAP,BLOCK stop;
```

### Multi-task example judges can see complete in one turn

```text
User: “Tell me the hostel policy and report the broken AC in LH-123, ground floor.”

1. POLICY      → Retrieve → answer with citation
2. MAINTENANCE → Validate required fields → LOW risk → ACT → ticket created
3. RESULT      → Cited policy answer + ticket ID/status returned + notification sent
```

Low-risk work is not delayed by unnecessary approval. Only missing information, user-controlled bookings and sensitive/high-impact outcomes introduce a pause.

## 3. Knowledge flywheel — uncertainty becomes governed improvement

![CampusFlow governed knowledge flywheel](images/campusflow-knowledge-flywheel.png)

_Presentation asset: [download the full-resolution PNG](images/campusflow-knowledge-flywheel.png). The editable Mermaid version follows._

```mermaid
flowchart LR
    Q["❓ User asks an uncovered policy question"]
    SEARCH["🔎 RAG search + exact-fact grounding check"]
    KNOW{"Sufficient verified evidence?"}
    SAFE["🤝 Honest uncertainty response"]
    TICKET["🎫 Deduplicated knowledge-gap request"]
    ADMIN["👩‍💼 Admin reviews demand and uploads policy"]
    VALIDATE["🛡️ Validate file, metadata and policy ID"]
    INDEX["📚 Add versioned document to live corpus"]
    RESOLVE["✅ Resolve knowledge gap"]
    NEXT["💡 Next user gets a grounded cited answer"]
    ANSWER["📖 Immediate cited answer"]

    Q --> SEARCH --> KNOW
    KNOW -->|yes| ANSWER
    KNOW -->|no| SAFE --> TICKET --> ADMIN --> VALIDATE --> INDEX --> RESOLVE --> NEXT

    classDef query fill:#172554,stroke:#60a5fa,color:#eff6ff;
    classDef trust fill:#042f2e,stroke:#2dd4bf,color:#f0fdfa;
    classDef improve fill:#3b0764,stroke:#c084fc,color:#faf5ff;
    class Q,ANSWER,NEXT query;
    class SEARCH,KNOW,SAFE,VALIDATE trust;
    class TICKET,ADMIN,INDEX,RESOLVE improve;
```

This is not model retraining. It is a controlled institutional learning loop: the assistant admits uncertainty, creates visible demand for missing knowledge, and becomes more useful only after an authorized administrator supplies a verified source.

## Decision matrix

| Scenario | Risk | Agent mode | Human involvement | User-visible proof |
| --- | --- | --- | --- | --- |
| Broken AC with complete location | Low | **ACT** | Not required to create | Ticket ID, `OPEN` status, optional photo |
| Library/lab booking | Medium | **ASK** | User confirms selection | Booking reference + audit trace |
| Certificate request | Medium | **APPROVE** | Academic admin issues it | Verification report + approval status |
| Harassment/ragging/safety report | High | **ESCALATE** | Authorized officer reviews | Confidential grievance reference |
| Conflicting or unauthorized action | Any | **STOP** | Depends on policy | Reason + safe alternative |
| Missing policy fact | Unknown | **KNOWLEDGE GAP** | Admin publishes source | Gap ID, later cited answer |

## Real implementation map

| Layer | Main implementation |
| --- | --- |
| UI and streaming chat | `frontend/src/main.jsx` |
| Authentication and role identity | `backend/app/auth/`, `backend/app/api/auth_routes.py` |
| Authoritative routing | `backend/app/agents/router.py` |
| Gemini proposal/vision adapter | `backend/app/agents/gemini_adapter.py`, `document_verification_service.py` |
| Policy retrieval | `backend/app/rag/retriever.py`, `backend/app/rag/documents/` |
| Governance gates | `backend/app/policies/`, `permissions/`, `risk/`, `autonomy/` |
| Controlled workflows | `backend/app/services/request_service.py`, `backend/app/api/routes.py` |
| Evidence, status and accountability | maintenance attachments, notifications and audit services |

## 20-second closing line

> “Most copilots stop at answers. CampusFlow closes the institutional loop: it understands the request, proves the policy, chooses the permitted autonomy level, performs the action, and leaves a status and audit trail humans can trust.”
