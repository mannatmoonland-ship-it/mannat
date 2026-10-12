# Mannat Moon Construction — Architecture and Diagrams

This document describes the repository's current implementation, based on
`app.py`, `snapshot_routes.py`, `snapshot_service.py`, `owner_scope.py`,
`permission_policy.py`, the templates, browser modules, and Firestore Rules.
It is a source-level map, not a claim that every optional external integration
has been configured or exercised in every environment.

## 1. System at a glance

```mermaid
flowchart TB
    subgraph Client["Browser"]
        User["Signed-in user"]
        Templates["Jinja pages and forms"]
        Modules["Page modules in static/"]
        FirebaseWeb["Firebase Web SDK<br/>Auth + Firestore reads"]
        User --> Templates
        Templates --> Modules
        Modules --> FirebaseWeb
    end

    subgraph Server["Flask application"]
        Flask["app.py<br/>page routes, API routes, validation"]
        SnapshotRoutes["snapshot_routes.py<br/>snapshot/config blueprint"]
        PermissionPolicy["permission_policy.py<br/>allowlisted permission compiler"]
        OwnerScope["owner_scope.py<br/>Owner Scope persistence rules"]
        SnapshotService["snapshot_service.py<br/>archive, verify, restore"]
        InMemory["InMemoryFirestoreStore<br/>explicit local/test adapter"]
        AdminStore["FirebaseFirestoreStore<br/>Firebase Admin SDK adapter"]
        Flask --> PermissionPolicy
        Flask --> OwnerScope
        Flask --> InMemory
        Flask --> AdminStore
        Flask --> SnapshotRoutes
        SnapshotRoutes --> SnapshotService
        SnapshotRoutes --> AdminStore
    end

    subgraph External["External services and durable resources"]
        Firestore[("Cloud Firestore")]
        FirebaseAuth["Firebase Authentication"]
        SMTP["SMTP mail service"]
        Photos["Google Photos"]
        Drive["Google Drive / Docs links"]
        LocalFiles[("Local instance/ and backups/ files")]
    end

    Modules -->|"Same-origin HTML/API requests"| Flask
    FirebaseWeb -->|"Authenticated, permission-scoped reads"| Firestore
    FirebaseWeb --> FirebaseAuth
    Flask -->|"Admin SDK reads/writes"| Firestore
    Flask -->|"Email delivery"| SMTP
    Flask -->|"Playwright-assisted URL extraction"| Photos
    Modules -. "validated drawing links; opened by user" .-> Drive
    SnapshotRoutes --> LocalFiles
    SnapshotService --> Firestore
```

### Runtime and trust boundaries

| Area | Responsibility | Important boundary |
|---|---|---|
| Browser templates | Render page structure and server-provided CSRF/session metadata | Do not contain Firebase Admin credentials |
| Browser modules | Render and validate page interactions; read selected collections through Firebase Web SDK; send mutations to Flask | Firestore Rules deny direct browser writes |
| `app.py` | Flask setup, session/account logic, page/API routes, validation and application-level mutations | APIs independently authenticate, authorize and validate writes |
| `snapshot_routes.py` | Registers the Snapshots blueprint and authenticated public Firebase Web config endpoint | Snapshot management is Super Admin-only and CSRF-protected |
| `permission_policy.py` | Compiles built-in/custom roles into an allowlisted set of permission identifiers | Unknown permissions and inactive users do not gain authority |
| `owner_scope.py` | Validates and performs Owner Scope item/payment mutations and reads | Uses Firestore transactions and validates the legacy-source metadata |
| `snapshot_service.py` | Encodes Firestore values, writes/verifies snapshot archives and restores data | Local archives and restore credentials are filesystem resources |
| `FirebaseFirestoreStore` | Uses Firebase Admin SDK for persistence and trusted writes | Admin SDK bypasses Firestore Rules; Flask authorization remains required |
| `InMemoryFirestoreStore` | In-memory implementation used when explicitly selected or injected | Non-durable; it is not the production persistence layer |
| `firestore.rules` | Verifies active Flask-linked Firebase sessions and permission claims for browser reads | Default deny; every listed collection denies browser writes |
| `wsgi.py` | Imports the existing Flask app and serves it through Waitress | Refuses insecure production startup configuration |

The application is primarily function- and module-oriented. Domain records are
Firestore documents (ordinary Python/JavaScript dictionaries), not ORM model
instances. The class diagram below therefore distinguishes implemented
classes from route/module responsibilities.

In the current source, the Drawings and Materials page-shell routes render their
templates behind `require_login`. Their Firebase bootstrap endpoints and
mutation APIs also independently require an authenticated session and apply
their own authorization controls; the page-shell behavior is shown separately
from data access in the workflows below.

## 2. Source and page/module map

### Server-side source

| Source | Main responsibility |
|---|---|
| `app.py` | Flask application, Firebase configuration loading, storage adapters, account/session logic, permission checks, validations, feature pages and APIs |
| `permission_policy.py` | Permission identifiers, built-in role permission sets and custom-role permission compilation |
| `owner_scope.py` | Owner Scope schema validation, metadata checks, reads, transactional create/update/delete, and payment handling |
| `snapshot_routes.py` | Snapshot UI/API, Firebase Web config route, credentials intake, restore preview/confirmation, project switching |
| `snapshot_service.py` | Firestore traversal, archive serialization, verification, restore batching and failure reporting |
| `wsgi.py` | Production Waitress entry point and startup configuration checks |
| `scripts/` | Explicit Owner Scope initialization, migration, finalization and split utilities; not normal web-request handlers |

### Pages and browser modules

| Feature/page | Template | Main browser code | Data path |
|---|---|---|---|
| Dashboard | `templates/dashboard.html` | Inline module, shared navigation and page assets | Flask-rendered page; supporting APIs/read paths as implemented |
| Trade Builder | `templates/index.html` | `static/app.js`, `static/contract_payment_summary.mjs`, `static/trade_payment_history.mjs`, `static/payment_sort.mjs` | Firebase Web reads; Flask trade/contract/payment mutations |
| Daily Reports | `templates/daily_reports.html` | `static/daily_reports.js`, `static/daily_report_sharing.js` | Firestore reads; Flask Daily Records APIs for mutations and helper data |
| Daily Report viewer template | `templates/daily_reports_viewer.html` | `static/daily_report_sharing.js` | Currently not rendered by any Flask route; the active share link targets the authenticated Daily Reports page |
| Drawings | `templates/drawings.html` | `static/drawings.js` | Firestore reads; Flask category/drawing mutations |
| Materials | `templates/materials.html` | `static/materials.js`, `static/materials_logic.mjs` | Firestore reads; Flask master-data and transaction mutations |
| Labour Payments | `templates/index.html` financial view and `templates/labour_payments.html` | Shared `static/app.js`/payment logic and page-specific template behavior | Trade reads; Flask labour-payment mutations |
| Owner Scope | `templates/owner_scope.html` | `static/owner_scope.js`, related sort/PDF/confirmation modules | Flask JSON APIs and `owner_scope.py` reads/mutations |
| Building Cost Summary | `templates/building_cost_summary.html` | `static/building_cost_summary.js`, `static/building_cost_summary_logic.mjs` | Permission-scoped Firestore reads and client-side summary calculation |
| Ledger / Payment Reports | `templates/ledger_prints.html` | `static/ledger_prints.js`, `static/ledger_pdf.js`, payment sorting/history utilities | Firestore reads; PDF generation in the browser |
| Snapshots | `templates/snapshots.html` | `static/snapshots.js` | Authenticated Flask APIs; local archive files and Admin SDK |
| Profile | `templates/profile_settings.html` | `static/profile_settings.js`, `static/animal_avatars.js` | Flask profile/avatar APIs |
| User management | `templates/user_management.html`, `templates/user_details.html` | Inline modules and Flask forms/APIs | Flask account/role management |
| Authentication/recovery | Login, reset, password-change and recovery templates | Form submissions to Flask | Flask session, user store, token/email flows |
| Google Photos | `templates/google_photos.html` | Form/UI behavior in Flask-rendered flow | Flask + Playwright browser pool + remote image URL |
| Help | User/developer guide templates | Shared layout/navigation | Server-rendered documentation pages |

Common browser utilities include `static/firebase_auth.js` (Firebase bootstrap),
`static/dialogs.js` (dialogs/notifications), `static/date_format.js`,
`static/animal_avatars.js`, and the shared design/navigation/style sheets.
`static/office_connection.js` and `static/office_connection.css` remain unused
legacy assets; they are no longer injected into rendered pages.
`static/old_daily_reports.js` is an older
parallel Daily Reports implementation; the active `daily_reports.html` loads
`daily_reports.js`.

### Shared browser asset inventory

| Group | Assets |
|---|---|
| Firebase and page behavior | `app.js`, `firebase_auth.js`, `daily_reports.js`, `old_daily_reports.js` (legacy), `drawings.js`, `materials.js`, `building_cost_summary.js`, `ledger_prints.js`, `owner_scope.js`, `profile_settings.js`, `snapshots.js`, `daily_report_sharing.js` |
| Reusable logic | `building_cost_summary_logic.mjs`, `contract_payment_summary.mjs`, `materials_logic.mjs`, `owner_scope_constants.mjs`, `owner_scope_data.mjs` (read helper, not imported by the active `owner_scope.js`), `owner_scope_delete_confirmation.mjs`, `owner_scope_sort.mjs`, `payment_sort.mjs`, `settlement_logic.mjs`, `trade_payment_history.mjs` |
| Shared utilities | `animal_avatars.js`, `date_format.js`, `dialogs.js`, `ledger_pdf.js`, `owner_scope_pdf.js` |
| Styles | `style.css`, `design_system.css`, `mm_navigation.css`, `dialogs.css`, `site_motion.css`, `dashboard.css`, `daily_reports.css`, `drawings.css`, `materials.css`, `building_cost_summary.css`, `ledger_prints.css`, `owner_scope.css`, `profile_settings.css`, `snapshots.css` |

The templates also include `_navigation.html`, `dashboard.html`,
`index.html`, `daily_reports.html`, `daily_reports_viewer.html`,
`drawings.html`, `materials.html`, `labour_payments.html`,
`building_cost_summary.html`, `ledger_prints.html`, `owner_scope.html`,
`snapshots.html`, `profile_settings.html`, `login.html`,
`password_reset_request.html`, `forgot_password.html`, `reset_password.html`,
`change_password.html`, `super_admin_recovery.html`,
`user_management.html`, `user_details.html`, `google_photos.html`,
`user_guide.html`, and `developer_guide.html`.

`daily_reports_viewer.html` is presently an unreferenced template, not an
available webpage route. The active share link points to `/daily-reports`,
which requires the Daily Reports permission. Do not expose the orphan viewer
publicly without an explicit authorization and data-disclosure design.

## 3. Class and module diagram

```mermaid
classDiagram
    direction LR

    class AppModule {
        <<module>>
        Flask application and configuration
        page route functions
        API route functions
        request validators
        permission and CSRF guards
    }
    class SnapshotRoutes {
        <<blueprint>>
        snapshot_page()
        firebase_web_config()
        snapshot APIs
    }
    class PermissionPolicy {
        <<module>>
        PERMISSION_IDENTIFIERS
        compile_effective_permissions()
    }
    class OwnerScopeModule {
        <<module>>
        get_owner_scope_data()
        create_owner_scope_item()
        update_owner_scope_item()
        delete_owner_scope_item()
    }
    class SnapshotService {
        <<module>>
        create_snapshot()
        verify_snapshot()
        restore_snapshot()
        SnapshotError
        RestoreFailure
    }
    class StoreAdapter {
        <<conceptual contract>>
        user and feature persistence operations
        not a declared Python base class
    }
    class InMemoryFirestoreStore {
        local/test implementation
        dict-backed collections
        mutation locks
    }
    class FirebaseFirestoreStore {
        Firebase Admin implementation
        project and credential configuration
        Firestore transactions
    }
    class BrowserPool {
        bounded Playwright browser reuse
    }
    class FirebaseWebBootstrap {
        <<JavaScript module>>
        getFirebaseConfig()
        getFirebaseApp()
        getFirebaseAuth()
        reauthenticateFirebase()
    }
    class FirebaseWebSDK {
        <<external SDK>>
        Firebase Auth
        Firestore client reads
    }
    class FirestoreRules {
        <<policy>>
        active session validation
        permission-scoped reads
        deny browser writes
    }
    class Firestore {
        <<external service>>
        documents and subcollections
    }
    class FlaskMail {
        <<extension>>
        SMTP delivery
    }

    AppModule --> PermissionPolicy : compiles/checks roles
    AppModule --> StoreAdapter : calls persistence operations
    InMemoryFirestoreStore ..|> StoreAdapter : matching behavior
    FirebaseFirestoreStore ..|> StoreAdapter : matching behavior
    AppModule --> OwnerScopeModule : delegates Owner Scope
    AppModule --> SnapshotRoutes : registers blueprint
    SnapshotRoutes --> SnapshotService : create/verify/restore
    SnapshotRoutes --> FirebaseFirestoreStore : active project DB
    AppModule --> BrowserPool : Google Photos extraction
    AppModule --> FlaskMail : reset/setup/test email
    FirebaseFirestoreStore --> Firestore : Admin SDK access
    FirebaseWebBootstrap --> FirebaseWebSDK : initializes
    FirebaseWebSDK --> FirestoreRules : evaluated on client request
    FirestoreRules --> Firestore : protects client reads
    FirebaseWebSDK --> Firestore : permitted reads
```

`StoreAdapter` is a diagram-level concept only: the repository does not declare
a Python abstract base class or formal protocol. The two store classes implement
the application operations independently. Likewise, domain entities are
document shapes rather than Python classes.

## 4. Firestore collection and relationship diagram

```mermaid
erDiagram
    USERS {
        string id PK
        string username
        string username_lower
        string role
        string status
        array active_sessions
        int authorization_version
        string password_hash
    }
    CUSTOM_STAFF_TYPES {
        string id PK
        string name
        array permissions
    }
    TRADES {
        string id PK
        string name
        array workerCategories
        array contractItems
        array paymentTransactions
    }
    DAILY_RECORDS {
        string id PK
        string date
        string tradeId
        string createdByUserId
        string updatedAt
    }
    MATERIAL_CATEGORIES {
        string id PK
        string name
        string unit
    }
    MATERIALS {
        string id PK
        string categoryId
        string name
        string unit
    }
    MATERIAL_BRANDS {
        string id PK
        string materialId
        string name
    }
    SUPPLIERS {
        string id PK
        array categoryIds
    }
    MATERIAL_PURCHASES {
        string id PK
        string categoryId
        string materialId
        string supplierId
        number quantity
        number totalAmount
    }
    MATERIAL_CONSUMPTIONS {
        string id PK
        string categoryId
        string materialId
        number quantity
    }
    DRAWINGS_CATEGORIES {
        string id PK
        string name
        int sortOrder
    }
    DRAWINGS {
        string id PK
        string categoryId
        string drawingName
        string googleDriveUrl
        int sortOrder
    }
    OWNER_SCOPES {
        string id PK
        string name
        object legacySource
    }
    OWNER_SCOPE_ITEMS {
        string id PK
        string name
        string category
        string sourceLegacyTradeId
    }
    OWNER_SCOPE_PAYMENTS {
        string id PK
        string expenseItemId
        number amount
        string paymentMode
    }
    AUDIT_LOG {
        string id PK
        string actor_id
        string action
        datetime timestamp
    }
    CAMERA_AUTH_RATE_LIMITS {
        string id PK
        number attempts
        datetime window
    }

    USERS ||--o{ AUDIT_LOG : "actor_id (logical)"
    USERS ||--o{ DAILY_RECORDS : "creator/editor IDs (logical)"
    TRADES ||--o{ DAILY_RECORDS : tradeId
    MATERIAL_CATEGORIES ||--o{ MATERIALS : categoryId
    MATERIAL_CATEGORIES }o--o{ SUPPLIERS : "categoryIds association"
    MATERIAL_CATEGORIES ||--o{ MATERIAL_PURCHASES : categoryId
    MATERIAL_CATEGORIES ||--o{ MATERIAL_CONSUMPTIONS : categoryId
    MATERIALS ||--o{ MATERIAL_BRANDS : materialId
    MATERIALS ||--o{ MATERIAL_PURCHASES : materialId
    MATERIALS ||--o{ MATERIAL_CONSUMPTIONS : materialId
    SUPPLIERS ||--o{ MATERIAL_PURCHASES : supplierId
    DRAWINGS_CATEGORIES ||--o{ DRAWINGS : categoryId
    OWNER_SCOPES ||--o{ OWNER_SCOPE_ITEMS : "items subcollection"
    OWNER_SCOPES ||--o{ OWNER_SCOPE_PAYMENTS : "payments subcollection"
    OWNER_SCOPE_ITEMS ||--o{ OWNER_SCOPE_PAYMENTS : expenseItemId
```

Relationships are logical references, not relational foreign-key constraints.
Trade contracts and labour payment transactions are stored within their parent
trade documents by the current store methods; Owner Scope items/payments are
Firestore subcollections. Field sets can have optional/legacy-compatible
members beyond those shown.

## 5. Authentication, authorization, and common request flows

### 5.1 Flask sign-in and session lifecycle

```mermaid
flowchart TD
    A["GET /login"] --> B["Render login form"]
    B --> C["POST username + password"]
    C --> D["Normalize username"]
    D --> E["Load user through configured store"]
    E --> F{"User exists, active,<br/>valid role/session data?"}
    F -- no --> G["Audit failed sign-in"]
    G --> H["Generic invalid-credentials response"]
    F -- yes --> I["Verify stored password hash"]
    I --> J{"Password valid?"}
    J -- no --> G
    J -- yes --> K["Create/reissue server session ID"]
    K --> L["Record session ID in user active_sessions"]
    L --> M["Set Flask session; redirect to app"]
    M --> N["Every protected request reloads user/session state"]
    N --> O{"Active account and session ID still allowed?"}
    O -- no --> P["Clear session / reject request"]
    O -- yes --> Q["Continue through route permission checks"]
```

The Flask session is the application login. A browser Firebase token is issued
only as a bridge for Firestore reads; it does not replace the Flask session.

### 5.2 Firebase Web bootstrap and Firestore read authorization

```mermaid
sequenceDiagram
    actor User
    participant Page as Browser page module
    participant AuthJS as firebase_auth.js
    participant Flask as Flask routes
    participant Admin as Firebase Admin SDK
    participant Auth as Firebase Authentication
    participant Rules as Firestore Rules
    participant DB as Cloud Firestore

    User->>Page: Open authenticated page
    Page->>AuthJS: await firebaseAuthReady
    AuthJS->>Flask: GET /api/firebase-web-config
    Flask-->>AuthJS: Public Web config, no-store
    AuthJS->>AuthJS: Import Firebase Web SDK; initialize app/auth
    AuthJS->>Flask: GET /api/firebase-custom-token
    Flask-->>AuthJS: Session-bound CSRF token
    AuthJS->>Flask: POST /api/firebase-custom-token + CSRF
    Flask->>Flask: Verify Flask session, status, permissions, claims
    Flask->>Admin: Create custom token for server-selected user
    Admin-->>Flask: Signed custom token
    Flask-->>AuthJS: Custom token
    AuthJS->>Auth: Sign in with custom token
    Auth-->>Page: Authenticated Firebase user
    Page->>DB: Read allowed collection(s)
    DB->>Rules: Evaluate token/session/permission claim
    Rules-->>DB: Allow scoped read or deny
    DB-->>Page: Read result or permission/network error
    Page->>Flask: Mutation API with session + feature CSRF
    Flask->>Flask: Re-check user, permission, CSRF, payload
    Flask->>Admin: Perform trusted write
    Admin->>DB: Create/update/delete
```

No Admin credential or private key belongs in the browser. Direct Web SDK
mutations are denied by `firestore.rules`; trusted mutations go through
permission-checked Flask routes.

### 5.3 Shared mutation API guard

```mermaid
flowchart LR
    Request["Browser mutation"] --> Session{"Valid Flask login?"}
    Session -- no --> Reject401["401 authentication required"]
    Session -- yes --> Password{"Must change password?"}
    Password -- yes --> Reject403a["403 change password first"]
    Password -- no --> Permission{"Required permission compiled and granted?"}
    Permission -- no --> Reject403b["403 forbidden"]
    Permission -- yes --> CSRF{"Feature CSRF token valid?"}
    CSRF -- no --> Reject403c["API-specific 400/403 request validation"]
    CSRF -- yes --> Validate["Validate exact fields, types and constraints"]
    Validate -- invalid --> Reject4xx["400/409/422 domain response"]
    Validate -- valid --> Store["Store adapter / domain transaction"]
    Store --> Result["Sanitized success or service error response"]
```

Exact status codes and tokens vary by API family; consult the route implementation
before building a client against an individual endpoint.

## 6. Feature workflow diagrams

### 6.1 Dashboard and navigation

```mermaid
flowchart TD
    A["GET /"] --> B["require_login"]
    B --> C{"Current user and active session valid?"}
    C -- no --> Login["Redirect to login"]
    C -- yes --> D["Render dashboard.html with current user"]
    D --> E["Shared _navigation.html"]
    E --> F["Construction: reports, trades, materials, drawings"]
    E --> G["Finance: labour payments, Owner Scope, cost summary, ledger"]
    E --> H["Management: user admin and snapshots for Super Admin"]
    E --> I["Tools, settings, help pages"]
```

### 6.2 Daily Reports

```mermaid
flowchart TD
    A["Open /daily-reports"] --> B["Require daily_reports.read"]
    B --> C["Render daily_reports.html + CSRF"]
    C --> D["daily_reports.js awaits Firebase bootstrap"]
    D --> E["Read daily_records under Firestore Rules"]
    D --> F["GET dates / trades / author avatars via Flask"]
    E --> G["Render calendar, records, filters and editor"]
    F --> G
    G --> H{"User action"}
    H -- create --> I["POST /api/daily-records + CSRF"]
    H -- edit --> J["PATCH /api/daily-records/{record_id} + CSRF"]
    H -- delete --> K["DELETE /api/daily-records/{record_id} + CSRF"]
    I --> L["Flask validates permission/payload; Admin SDK writes"]
    J --> L
    K --> L
    L --> M["Refresh selected date/list"]
    G --> N["Optional share-image SVG route"]
```

### 6.3 Drawings

```mermaid
flowchart TD
    A["Open /drawings"] --> B["Render template and issue drawings CSRF"]
    B --> C["drawings.js awaits authenticated Firebase bootstrap"]
    C --> D["Config and custom-token routes require Flask login"]
    D --> E["Read drawings_categories and drawings"]
    E --> F["Render categories, filters and drawing list"]
    F --> G{"Category action"}
    G -- create --> H["POST /api/drawings/categories"]
    G -- edit/delete --> I["PATCH/DELETE /api/drawings/categories/{id}"]
    G -- reorder --> J["POST category order"]
    F --> K{"Drawing action"}
    K -- create --> L["POST /api/drawings"]
    K -- edit/delete --> M["PATCH/DELETE /api/drawings/{id}"]
    K -- reorder --> N["POST category drawing order"]
    H --> O["Flask auth + drawings.write + CSRF + validation"]
    I --> O
    J --> O
    L --> P["Flask validates fields and allowed Drive/Docs URL"]
    M --> O
    N --> O
    P --> Q["Store adapter mutates Firestore"]
    O --> Q
    Q --> R["Refresh page state and list"]
```

### 6.4 Materials

```mermaid
flowchart TD
    A["Open /materials"] --> B["Render template and issue material CSRF"]
    B --> C["materials.js awaits authenticated Firebase bootstrap"]
    C --> D["Config and custom-token routes require Flask login"]
    D --> E["Read categories, materials, brands,<br/>suppliers, purchases and consumptions"]
    E --> F["Render category/supplier/stock tabs and balances"]
    F --> G{"Master-data action"}
    G -- category --> H["POST/PATCH/DELETE /api/material-master/categories"]
    G -- supplier --> I["POST/PATCH/DELETE /api/material-master/suppliers"]
    H --> J["Flask permission + CSRF + schema validation"]
    I --> J
    F --> K{"Transaction action"}
    K -- purchase --> L["POST/PATCH/DELETE /api/material-transactions/purchases"]
    K -- consumption --> M["POST/PATCH/DELETE /api/material-transactions/consumptions"]
    L --> N["Validate IDs, quantities, supplier/category and duplicate request"]
    M --> O["Validate units, available stock and transaction history"]
    N --> P["Transactional Admin SDK/store mutation"]
    O --> P
    J --> P
    P --> Q["Reload records, recalculate totals and render status"]
```

Brand records/associations are read from the Firestore material data model and
are coordinated through the Materials workflows as implemented; the listed
master-data APIs in `app.py` expose category and supplier operations. Do not
infer an independent public brand CRUD API from the diagram.

### 6.5 Trades, contract values and labour payments

```mermaid
flowchart TD
    A["Open Trade Builder / Labour Payments"] --> B["Require page access"]
    B --> C["app.js awaits Firebase bootstrap"]
    C --> D["Read trades and authorized supporting records"]
    D --> E["Render trades, worker categories and payment history"]
    E --> F{"Trade configuration"}
    F -- create/edit/delete/rename --> G["/api/trades APIs"]
    E --> H{"Contract values"}
    H -- add/edit/delete/replace --> I["/api/trades/{trade_id}/contract-items APIs"]
    E --> J{"Labour payment"}
    J -- create --> K["POST /api/labour-payments/{trade_id}/transactions"]
    J -- edit/delete --> L["PATCH/DELETE transaction API"]
    G --> M["Flask permission, CSRF and payload validation"]
    I --> M
    K --> N["Validate payment type, worker/date/amount and request ID"]
    L --> N
    M --> O["Store adapter updates trade document"]
    N --> O
    O --> P["Refresh trade/payment UI"]
    P --> Q["Contract/payment summaries and ledger/PDF views"]
```

Trades, their contract items and labour payment transactions are managed
through the trade storage operations. Summary calculations and sorting have
client-side helper modules with corresponding tests.

### 6.6 Owner Scope and Building Cost Summary

```mermaid
flowchart TD
    A["Open /owner-scope"] --> B["Require Flask login"]
    B --> C["Render owner_scope.html + CSRF"]
    C --> D["owner_scope.js GET /api/owner-scope"]
    D --> E["Flask checks owner_scope.read"]
    E --> F["owner_scope.py validates metadata;<br/>Admin SDK reads items and payments"]
    F --> G["Render expense items and payment history"]
    G --> H{"Item action"}
    H -- create --> I["POST /api/owner-scope/items"]
    H -- edit --> J["PUT /api/owner-scope/items/{id}"]
    H -- delete --> K["DELETE /api/owner-scope/items/{id}"]
    I --> L["Check permission + CSRF; validate item and initial payment"]
    J --> M["Check permission + CSRF; update item and linked payment"]
    K --> N["Check permission + CSRF; delete item and linked payments"]
    L --> O["owner_scope.py persists item/payment atomically"]
    M --> O
    N --> O
    O --> P["Reload /api/owner-scope and render"]
    Q["Building Cost Summary page"] --> R["Require building_cost_summary.read"]
    R --> S["Await Firebase bootstrap"]
    S --> T["Read permitted trades, material categories/purchases,<br/>Owner Scope payments"]
    T --> U["building_cost_summary_logic.mjs computes totals"]
    U --> V["Render summary/export"]
```

Owner Scope retains a validated reference to a designated legacy Owner Account
trade for migration/compatibility. It is not a general trade-to-scope foreign
key.

### 6.7 Ledger and reports

```mermaid
flowchart TD
    A["Open /ledger-prints"] --> B["Require ledger.read"]
    B --> C["ledger_prints.js awaits Firebase bootstrap"]
    C --> D["Read permitted trade/payment records"]
    D --> E["Filter/sort and render ledger"]
    E --> F["ledger_pdf.js / jsPDF exports"]
    G["Open /building-cost-summary"] --> H["Require building_cost_summary.read"]
    H --> I["Read permitted Firestore collections"]
    I --> J["Calculate and render cost summary"]
```

### 6.8 Authentication recovery, profile and account administration

```mermaid
flowchart TD
    A["Sign-in / recovery / profile route"] --> B{"Operation"}
    B -- sign-in --> C["Lookup normalized username; check active status and password hash"]
    C --> D["Set Flask session; persist active session ID"]
    B -- password reset request --> E["Rate-limit request and use generic response"]
    E --> F["Create expiring token state; send link using configured SMTP"]
    F --> G["Validate one-time reset token"]
    G --> H["Consume token and store new password hash"]
    B -- change password --> I["Authenticated user validates current/new password"]
    I --> H
    B -- profile/avatar --> J["Authenticated API + CSRF + allowlisted avatar"]
    J --> K["Update user document"]
    B -- user administration --> L["Super Admin routes and role/permission validation"]
    L --> M["Update users/custom_staff_types; audit changes"]
    D --> N["New authenticated application session"]
    H --> N
```

SMTP connectivity and Firebase service availability are environment-dependent;
this flow shows the intended code path, not a claim of successful delivery.

### 6.9 Snapshots and project restore/switch

```mermaid
flowchart TD
    A["Super Admin opens /snapshots"] --> B["Create snapshot CSRF"]
    B --> C["GET /api/snapshots"]
    C --> D["List local archives and verification metadata"]
    D --> E{"Operation"}
    E -- create --> F["POST snapshot API"]
    F --> G["Admin SDK traverses Firestore documents/subcollections"]
    G --> H["snapshot_service encodes values into ZIP archive"]
    H --> I["Verify archive; write local audit event"]
    E -- verify --> J["Validate path and verify archive contents"]
    E -- restore preview --> K["Upload destination credentials + public Web config"]
    K --> L["Validate archive, project IDs, destination type and credential file"]
    L --> M["Write credential file with private local permissions"]
    M --> N["Preview destination and record pending restore token"]
    N --> O["Confirm restore"]
    O --> P["Restore in bounded Firestore batches; report results"]
    E -- switch --> Q["Select eligible restored project"]
    Q --> R["Save active Firebase project configuration"]
```

Snapshot archives, restore audit logs and staged credential files live on the
application host's filesystem. Their persistence depends on the host's storage
configuration.

### 6.10 Google Photos and camera authentication

```mermaid
flowchart TD
    A["Google Photos page/request"] --> B["Require login and google_photos.use"]
    B --> C["Validate supplied shared URL"]
    C --> D["BrowserPool borrows headless Playwright browser"]
    D --> E["Load remote page and extract direct image URL"]
    E --> F["Cache URL within configured bounded cache"]
    F --> G["Return result / image proxy response"]
    G --> H["Return browser to pool or close it"]
    I["Camera client"] --> J["POST /api/camera/authenticate"]
    J --> K["Validate configured secret/assertion and rate limits"]
    K --> L["Return bounded authentication result"]
```

## 7. Representative object diagrams

The IDs and values below are synthetic labels used only to explain object
relationships. They are not records copied from the configured database.

### 7.1 Authenticated user and session

```mermaid
flowchart LR
    U["user:u_example<br/>role = supervisor<br/>status = active<br/>active_sessions = [session:s_example]<br/>authorization_version = 4"]
    S["flask_session:s_example<br/>user_id = u_example<br/>session_id = s_example"]
    C["firebase_custom_claims<br/>sid = s_example<br/>ver = 4<br/>perms = compiled allowlisted set"]
    R["Firestore Rules decision<br/>active user + session/version + permission"]
    U -->|"server reads and validates"| S
    U -->|"server derives, never client supplied"| C
    S -->|"backs Flask request identity"| C
    C --> R
```

### 7.2 Materials and stock transaction records

```mermaid
flowchart LR
    Cat["material_categories/cat_example<br/>name, unit"]
    Mat["materials/mat_example<br/>categoryId = cat_example<br/>name"]
    Brand["material_brands/brand_example<br/>materialId = mat_example"]
    Supplier["suppliers/supplier_example<br/>category association"]
    Purchase["material_purchases/purchase_example<br/>categoryId, materialId,<br/>supplierId, quantity, totalAmount"]
    Consumption["material_consumptions/consumption_example<br/>categoryId, materialId,<br/>quantity, unit"]
    Cat --> Mat
    Mat --> Brand
    Cat --> Purchase
    Mat --> Purchase
    Supplier --> Purchase
    Cat --> Consumption
    Mat --> Consumption
```

### 7.3 Construction records and payments

```mermaid
flowchart LR
    Trade["trades/trade_example<br/>name, workerCategories"]
    Contract["contractItems[]<br/>id, name, amount"]
    Payment["paymentTransactions[]<br/>id, date, amount, worker/type"]
    Daily["daily_records/report_example<br/>date, tradeId, work/materials,<br/>creator/editor attribution"]
    Trade -->|"contains"| Contract
    Trade -->|"contains"| Payment
    Trade -->|"logical tradeId reference"| Daily
```

### 7.4 Drawings library

```mermaid
flowchart LR
    Category["drawings_categories/category_example<br/>name, sortOrder"]
    Drawing["drawings/drawing_example<br/>categoryId, drawingName,<br/>number, revision, Drive URL,<br/>sortOrder, timestamps"]
    Category -->|"categoryId"| Drawing
```

### 7.5 Owner Scope

```mermaid
flowchart LR
    Scope["owner_scopes/owner-scope<br/>name = Owner Scope<br/>legacySource = validated legacy trade"]
    Item["owner_scopes/owner-scope/items/item_example<br/>name, category, remarks"]
    Pay["owner_scopes/owner-scope/payments/payment_example<br/>expenseItemId, date, amount,<br/>paymentMode, remarks"]
    Legacy["trades/{designated legacy ID}<br/>legacy Owner Account source"]
    Scope -->|"contains subcollection"| Item
    Scope -->|"contains subcollection"| Pay
    Pay -->|"expenseItemId"| Item
    Scope -. "legacySource metadata checked against" .-> Legacy
```

### 7.6 Snapshot archive

```mermaid
flowchart LR
    Archive["local ZIP snapshot"]
    Metadata["metadata.json<br/>format version, project ID,<br/>created time, collection counts"]
    Documents["documents.jsonl<br/>collection path, document ID,<br/>encoded Firestore data"]
    Audit["local snapshot audit event"]
    Archive --> Metadata
    Archive --> Documents
    Audit -. "records snapshot operation" .-> Archive
```

## 8. Route/API inventory

This is grouped by route family to make the public surface navigable; variable
segments such as IDs are shown symbolically.

| Route family | Operations |
|---|---|
| Health/static | `GET/HEAD /health`, `/favicon.ico`, static asset delivery |
| Main pages | `/`, `/profile-settings`, `/trade-builder`, `/ledger-prints`, `/drawings`, `/materials`, `/building-cost-summary`, `/labour-payments`, `/owner-scope`, `/daily-reports`, `/google-photos`, `/user-guide`, `/developer-guide` |
| Firebase bridge | `GET /api/firebase-web-config`; `GET/POST /api/firebase-custom-token` |
| Authentication and recovery | `/login`, `/logout`, `/password-reset-request`, `/reset-password/{user_id}`, `/reset-password/{user_id}/validate`, `/forgot-password`, `/change-password`, `/super-admin-recovery`, `/test-email` |
| Profile and camera | `PATCH /api/profile`, `POST /api/profile/avatar`, `POST /api/camera/authenticate` |
| User administration | `/user-management`, `/user-management/create`, `/user-management/custom-type` and `/{staff_type_id}/edit|delete`, `/user-management/{user_id}`, `/{user_id}/edit`, `/{user_id}/toggle-status`, `/{user_id}/reset-password` |
| Drawings | Categories create/update/delete/order; drawings create/update/delete; category drawing order under `/api/drawings/...` |
| Materials | Category and supplier create/update/delete under `/api/material-master/...`; purchase/consumption create/update/delete under `/api/material-transactions/{kind}...` |
| Trades/contracts | Trade create/update/delete/rename under `/api/trades`; contract item create/replace/update/delete under `/api/trades/{trade_id}/contract-items...` |
| Labour payments | Create/update/delete under `/api/labour-payments/{trade_id}/transactions...` |
| Owner Scope | `GET /api/owner-scope`; item create `POST /api/owner-scope/items`; item `PUT/DELETE /api/owner-scope/items/{item_id}`. Initial and linked payment records are managed by item operations; there is no separate payment CRUD route in this API family |
| Daily Records | Dates, author avatars, trade options, create/update/delete under `/api/daily-records...`; share image under `/daily-reports/share-image` |
| Snapshots | `/snapshots`, `/api/snapshots` create/list, verify/delete/open-folder, restore preview/confirmation, switch-project/switch-back |
| Google Photos | `/google-photos`, `/extract-google-photos-url`, `/image` |

Authorization is not uniform solely by URL family. Feature APIs apply their
own permission and CSRF checks; use the corresponding route handler as the
source of truth for required permissions, methods, payloads and status codes.

## 9. Firestore Rules summary

`firestore.rules` validates the Firebase user's UID against the Flask-owned
`users/{uid}` record and checks:

1. A non-null Firebase auth token.
2. An active session ID claim still present in the user's `active_sessions`.
3. An active account and no forced password change.
4. Matching authorization-version claim.
5. The required permission claim for the target read.

Rules allow only selected `get/list` operations for the modules in use. They
explicitly deny direct browser writes and end with a catch-all deny. Admin SDK
requests bypass these rules and therefore rely on Flask route authorization and
domain validation.

## 10. Local configuration and test boundaries

- `python app.py` runs the Flask development server as documented in `README.txt`.
- `wsgi.py` is the Waitress entry point and validates secure production startup
  settings before serving.
- `FIREBASE_PROJECT_ID`, public `FIREBASE_WEB_CONFIG`, and Firebase Admin
  credentials configure the Firebase integration. Keep Admin credentials and
  `.env` contents out of browser assets and source-control artifacts.
- `FIRESTORE_MODE=memory` / explicit in-memory store injection is for isolated
  testing only; it is not persistent Firebase readiness.
- `firebase.json` configures the Firestore Rules emulator on port 8080. The
  repository's `package.json` exposes `npm run test:rules`.
- Python route/domain tests are in `tests/test_*.py`; JavaScript logic and Rules
  tests are in `tests/*.mjs`.
- `instance/` and `backups/` are local filesystem storage and are not durable
  cloud storage by themselves.

## 11. Change-impact guide

| Change area | Review alongside |
|---|---|
| Authentication/session | `app.py`, `permission_policy.py`, `firestore.rules`, `static/firebase_auth.js`, auth tests |
| Permission identifiers/roles | `permission_policy.py`, Flask feature guards, custom token claims, `firestore.rules`, permission and rules tests |
| Firestore collection/schema | Both store adapters, feature validators/routes, browser read queries, Rules, migrations and relevant tests |
| Materials calculations | `app.py` transaction/domain checks, `static/materials.js`, `static/materials_logic.mjs`, materials API and logic tests |
| Trade/payments | Trade store methods, trade/payment API guards and validators, `static/app.js`, calculation/sort helpers and tests |
| Owner Scope | `owner_scope.py`, routes in `app.py`, `static/owner_scope*.{js,mjs}`, Firestore Rules and migration tests |
| Snapshot format/restore | `snapshot_service.py`, `snapshot_routes.py`, `static/snapshots.js`, snapshot tests and deployment/storage expectations |
| A browser page | Its template, loaded browser modules/styles, APIs, authorization/CSRF checks and matching tests |
