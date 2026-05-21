## System Architecture Overview

### Architecture Pattern
**Layered (N-Tier) Architecture** with clear separation of concerns:
- Presentation Layer (API routes)
- Business Logic Layer (services)
- Data Access Layer (repositories)
- Database Layer

### High-Level Architecture Diagram

```
[Client Application]
        │
        │ HTTPS/REST
        ▼
[Load Balancer / Reverse Proxy - Nginx]
        │
        ▼
[FastAPI Application - Containerized]
        │
        ├─── [Authentication Middleware]
        ├─── [Rate Limiting Middleware]
        ├─── [Logging Middleware]
        │
        ▼
[Service Layer]
        │
        ├─── User Service
        ├─── Board Service
        ├─── Column Service
        ├─── Task Service
        ├─── Activity Service
        │
        ▼
[Repository Layer]
        │
        ▼
[Database - PostgreSQL]
        │
        └─── [Redis Cache - Optional]
```

## Component Architecture

### 1. Presentation Layer (API Routes)
- **RESTful endpoints** organized by resource
- **Request validation** using Pydantic models
- **Response serialization** using Pydantic
- **Dependency injection** for services
- **API versioning** (e.g., `/api/v1/`)

### 2. Middleware Stack
- **Authentication Middleware**: JWT token validation
- **Authorization Middleware**: Role/permission checking
- **Rate Limiting Middleware**: Request throttling per user/IP
- **Logging Middleware**: Request/response logging
- **CORS Middleware**: Cross-origin resource sharing
- **Error Handling Middleware**: Centralized exception management

### 3. Service Layer (Business Logic)
- **UserService**: Registration, authentication, profile management
- **BoardService**: Board CRUD operations, ownership validation
- **ColumnService**: Column management, position reordering
- **TaskService**: Task operations, cross-column moves, filtering
- **ActivityService**: Audit logging, change tracking

### 4. Repository Layer (Data Access)
- **BaseRepository**: Generic CRUD operations
- **UserRepository**: User-specific database queries
- **BoardRepository**: Board with relationships
- **ColumnRepository**: Column ordering queries
- **TaskRepository**: Complex task filtering and search

### 5. Database Layer
- **Primary Database**: PostgreSQL (relational data)
- **Connection Pool**: Asyncpg or SQLAlchemy pool
- **Migrations**: Alembic for schema version control

## Data Architecture

### Core Entities
- **User**: id, email, username, hashed_password, created_at, updated_at
- **Board**: id, title, description, owner_id, created_at, updated_at, deleted_at (soft delete)
- **Column**: id, board_id, name, position, created_at, updated_at
- **Task**: id, column_id, title, description, priority, due_date, assignee_id, position, created_at, updated_at
- **Comment**: id, task_id, user_id, content, created_at
- **Label**: id, name, color
- **TaskLabel**: task_id, label_id (junction table)
- **ActivityLog**: id, user_id, task_id, action, old_value, new_value, created_at
- **BoardMember**: board_id, user_id, role (junction table for collaboration)

### Relationships
- User (1) → (N) Board (owner)
- User (N) ↔ (N) Board (members)
- Board (1) → (N) Column
- Column (1) → (N) Task
- User (1) → (N) Task (assignee)
- Task (1) → (N) Comment
- Task (N) ↔ (N) Label
- Task (1) → (N) ActivityLog

## Security Architecture

### Authentication Flow
1. Client submits credentials to `/auth/login`
2. Server validates and returns JWT access + refresh tokens
3. Client includes JWT in `Authorization: Bearer <token>` header
4. Middleware validates token on each protected request

### Authorization Strategy
- **Role-Based Access Control (RBAC)**
  - Board Owner: full control (CRUD board, manage members)
  - Board Member: create/edit tasks, add comments
  - Viewer: read-only access

### Data Protection
- **Password Hashing**: bcrypt (cost factor 12)
- **JWT Secret**: environment variable, minimum 32 bytes
- **SQL Injection Prevention**: Parameterized queries via ORM
- **XSS Prevention**: Output encoding, CORS policies

## Scalability Architecture

### Horizontal Scaling Strategy
- **Stateless Application**: All session data in JWT
- **Multiple FastAPI instances** behind load balancer
- **Database connection pooling** per instance
- **Read replicas** for reporting and read-heavy operations

### Caching Strategy
- **Redis Cache** for:
  - User session validation
  - Frequently accessed boards (TTL: 5 minutes)
  - API rate limiting counters
  - Query result caching for dashboard views

### Asynchronous Processing
- **Background Tasks** via Celery or FastAPI BackgroundTasks:
  - Email notifications
  - Activity log aggregation
  - Report generation

## Deployment Architecture

### Container Architecture (Docker)
```
[Application Container]
  - FastAPI + Uvicorn
  - Python runtime
  
[Database Container]
  - PostgreSQL
  
[Cache Container]
  - Redis
  
[Reverse Proxy Container]
  - Nginx (SSL termination, static files)
```

### Environment Configuration
- **Development**: Docker Compose (local)
- **Staging**: Kubernetes or Docker Swarm
- **Production**: Cloud-native (AWS ECS/AKS/GKE)

### Service Discovery
- Environment variables for internal service URLs
- DNS-based discovery for container orchestration

## API Design Architecture

### RESTful Resource Hierarchy
```
/api/v1/
├── auth/
│   ├── login
│   ├── logout
│   └── refresh
├── users/
│   ├── me
│   └── {user_id}
├── boards/
│   ├── {board_id}
│   ├── {board_id}/columns
│   ├── {board_id}/tasks
│   └── {board_id}/members
├── columns/
│   └── {column_id}/tasks
├── tasks/
│   ├── {task_id}
│   ├── {task_id}/comments
│   └── {task_id}/move
└── labels/
```

### Communication Patterns
- **Synchronous**: REST HTTP requests
- **Asynchronous**: WebSocket for real-time updates (optional)
- **Event-Driven**: Webhooks for external integrations

## Data Flow Architecture

### Create Task Flow
1. Client POST `/boards/{id}/tasks` with task data
2. API validates request → Authentication Middleware
3. BoardService verifies user has write access
4. TaskService creates task in specified column
5. Repository saves to database
6. ActivityService logs "task_created" event
7. Response returns created task with 201 status

### Move Task Flow
1. Client PATCH `/tasks/{id}/move` with target column/position
2. Authentication + Authorization checks
3. TaskService validates move permissions
4. Database transaction begins
5. Update task column_id and position
6. Reorder affected tasks in source and target columns
7. Commit transaction
8. Log activity asynchronously
9. Return updated task

## Monitoring Architecture

### Metrics Collection
- **Application Metrics**: Prometheus client library
  - Request count, duration, error rate
  - Active users per board
  - Task creation rate
- **System Metrics**: cAdvisor or Docker stats
  - CPU, memory, network I/O
- **Database Metrics**: PostgreSQL exporter

### Logging Pipeline
- **Structured logs** (JSON format) → stdout
- **Log aggregation** → ELK Stack (Elasticsearch, Logstash, Kibana)
- **Log levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL

### Alerting
- **SLO-based alerts** via Alertmanager
  - API error rate > 5% over 5 minutes
  - 95th percentile latency > 500ms
  - Database connection pool exhaustion

## Backup & Recovery Architecture

### Database Backup
- **Automated daily** full backups (pg_dump)
- **Continuous WAL archiving** for point-in-time recovery
- **Retention policy**: 30 days daily, 12 months monthly

### Disaster Recovery
- **RPO (Recovery Point Objective)**: 5 minutes
- **RTO (Recovery Time Objective)**: 1 hour
- **Multi-region replication** for critical deployments

## Integration Architecture

### External Services (Optional)
- **Email Service**: SMTP or SendGrid for notifications
- **File Storage**: S3-compatible for task attachments
- **Webhook System**: Outgoing webhooks for task events
- **OAuth Providers**: Google, GitHub authentication

### API Gateway Pattern
- Single entry point for all clients
- Request aggregation for complex queries
- Response caching and transformation

## Performance Optimization Architecture

### Database Optimization
- **Indexes**: Foreign keys, frequently queried fields (priority, due_date)
- **Partial indexes**: For soft-deleted records
- **Composite indexes**: (board_id, position), (column_id, position)
- **Query optimization**: Avoid N+1 queries with eager loading

### Application Optimization
- **Async database drivers** (asyncpg, databases)
- **Response compression** (GZip middleware)
- **Pagination** for all list endpoints (cursor or offset-based)
- **Batch operations** for bulk task moves

### Caching Layers
- **First level**: In-memory cache (TTL: 30 seconds)
- **Second level**: Redis distributed cache
- **Cache invalidation**: Write-through on task/board updates