# Cloud-Forge Lab

A backend application built with **FastAPI** and **PostgreSQL**, designed as a practical cloud-computing lab for understanding how an application evolves from a local environment toward **IaaS, containerization, scaling, and cloud infrastructure**.

The application provides a simple REST API for creating and retrieving users.

---

## Project Objective

The main purpose of this project is not just to build a REST API, but to use the same application to understand different infrastructure layers.

The project will progressively evolve through:

```text
Local Application
       ↓
Virtual Machine / IaaS
       ↓
Docker Containers
       ↓
Multiple Application Instances
       ↓
Load Balancing
       ↓
Cloud Storage & Database
       ↓
Monitoring & High Availability
       ↓
Kubernetes
```

The application code should remain mostly unchanged while the underlying infrastructure evolves.

---

## Current Architecture

At the current stage, the application runs locally:

```text
Client
   │
   ▼
FastAPI
   │
   ▼
SQLAlchemy
   │
   ▼
PostgreSQL
```

The complete local environment is:

```text
Linux
 ├── Python
 │    └── FastAPI
 │         └── SQLAlchemy
 │
 └── PostgreSQL
```

---

## Technologies

* **Python**
* **FastAPI** — REST API framework
* **Uvicorn** — ASGI server
* **SQLAlchemy** — ORM/database abstraction
* **PostgreSQL** — relational database
* **Pydantic** — request/response validation
* **python-dotenv** — environment configuration
* **Git/GitHub** — version control

---

## Project Structure

```text
fastapi-postgres-app/
│
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   ├── schemas.py
│   │
│   └── routers/
│       └── users.py
│
├── .env
├── requirements.txt
├── README.md
└── .gitignore
```

### `app/main.py`

The main FastAPI application.

Responsibilities:

* Creates the FastAPI application
* Creates database tables
* Registers API routers
* Defines the root endpoint

### `app/database.py`

Handles the PostgreSQL connection.

Responsibilities:

* Loads the database URL
* Creates the SQLAlchemy engine
* Creates database sessions
* Provides database dependencies to API routes

### `app/models.py`

Contains SQLAlchemy database models.

Currently, it defines the `User` table:

```text
users
├── id
├── name
└── email
```

### `app/schemas.py`

Contains Pydantic schemas used for API validation.

Currently:

* `UserCreate`
* `UserResponse`

### `app/routers/users.py`

Contains the user-related API endpoints.

Current operations:

```text
POST /users/
GET  /users/
```

### `.env`

Stores environment-specific configuration such as the PostgreSQL connection string.

Example:

```env
DATABASE_URL=postgresql://fastapi_user:your_password@localhost:5432/fastapi_db
```

The `.env` file should **not** be committed to Git because it may contain credentials.

---

## Requirements

Make sure the following are installed:

* Python 3
* PostgreSQL
* `pip`
* `venv`

---

## Installation

### 1. Clone the repository

```bash
git clone <repository-url>
cd fastapi-postgres-app
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
```

Activate it:

```bash
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## PostgreSQL Setup

Start PostgreSQL:

```bash
sudo systemctl start postgresql
```

Create the database:

```sql
CREATE DATABASE fastapi_db;
```

Create the application user:

```sql
CREATE USER fastapi_user WITH PASSWORD 'your_password';
```

Grant access:

```sql
GRANT ALL PRIVILEGES ON DATABASE fastapi_db TO fastapi_user;
```

---

## Environment Configuration

Create a `.env` file in the project root:

```env
DATABASE_URL=postgresql://fastapi_user:your_password@localhost:5432/fastapi_db
```

Make sure `.env` is included in `.gitignore`:

```text
.env
venv/
__pycache__/
```

---

## Running the Application

Activate the virtual environment:

```bash
source venv/bin/activate
```

Start the FastAPI server:

```bash
uvicorn app.main:app --reload
```

The application will run at:

```text
http://127.0.0.1:8000
```

---

## API Documentation

FastAPI automatically provides interactive API documentation.

Open:

```text
http://127.0.0.1:8000/docs
```

You can test the API directly from the Swagger UI.

---

## API Endpoints

### Root

```http
GET /
```

Response:

```json
{
  "message": "FastAPI PostgreSQL application is running"
}
```

---

### Create User

```http
POST /users/
```

Request:

```json
{
  "name": "Saad",
  "email": "saad@example.com"
}
```

Example response:

```json
{
  "id": 1,
  "name": "Saad",
  "email": "saad@example.com"
}
```

---

### Get Users

```http
GET /users/
```

Example response:

```json
[
  {
    "id": 1,
    "name": "Saad",
    "email": "saad@example.com"
  }
]
```

---

## Database Flow

When a user is created:

```text
HTTP Request
     │
     ▼
FastAPI Router
     │
     ▼
Pydantic Validation
     │
     ▼
SQLAlchemy
     │
     ▼
PostgreSQL
     │
     ▼
Response
```

This separation demonstrates the relationship between the application layer and the database layer.

---

## Cloud Computing Learning Path

This project is intentionally designed to evolve rather than be replaced by separate applications.

### Stage 1 — Local Infrastructure

Current stage:

```text
Laptop
 ├── Linux
 ├── Python
 ├── FastAPI
 └── PostgreSQL
```

Focus:

* Application architecture
* Database connectivity
* Environment configuration
* REST APIs

---

### Stage 2 — IaaS

The application will be moved to an Ubuntu virtual machine.

Target architecture:

```text
Physical Machine
      │
      ▼
Virtualization Layer
      │
      ▼
Ubuntu VM
      │
      ├── FastAPI
      └── PostgreSQL
```

Concepts:

* Virtual machines
* Compute resources
* CPU/RAM allocation
* Virtual networking
* Operating-system management
* IaaS responsibility boundaries

---

### Stage 3 — Containerization

The application will be separated into containers:

```text
Docker Network
│
├── Nginx
│
├── FastAPI
│
└── PostgreSQL
```

Concepts:

* Containers
* Images
* Container networking
* Volumes
* Environment variables
* Service isolation

---

### Stage 4 — Scaling

Multiple FastAPI instances will be introduced:

```text
                ┌── FastAPI Instance 1
Client → Load Balancer
                ├── FastAPI Instance 2
                │
                └── FastAPI Instance 3
                         │
                         ▼
                    PostgreSQL
```

Concepts:

* Horizontal scaling
* Load balancing
* Stateless applications
* Availability
* Resource utilization

---

### Stage 5 — Cloud Deployment

The infrastructure can eventually be mapped onto cloud services:

```text
Internet
    │
    ▼
Load Balancer
    │
    ├── Application Instance
    ├── Application Instance
    └── Application Instance
             │
             ▼
       Managed Database
             │
             ▼
        Cloud Storage
```

This stage introduces:

* Cloud compute
* Managed databases
* Cloud storage
* Networking
* Availability
* Monitoring
* Infrastructure abstraction

---

## Important Infrastructure Principle

The application should remain relatively stable while the infrastructure changes.

For example, the database connection is controlled through:

```env
DATABASE_URL=...
```

Therefore:

```text
Local PostgreSQL
       ↓
Docker PostgreSQL
       ↓
VM PostgreSQL
       ↓
Managed Cloud PostgreSQL
```

can be achieved primarily through configuration changes rather than rewriting the application.

This demonstrates an important cloud-computing principle:

> **Separate application logic from infrastructure configuration.**

---

## Future Improvements

Planned extensions include:

* Dockerizing the application
* Adding Nginx
* Running the application inside a VM
* PostgreSQL persistent storage
* Database backup and recovery
* Multiple FastAPI instances
* Load balancing
* Monitoring and logging
* Health-check endpoints
* Cloud deployment
* Managed database integration
* Kubernetes deployment

---

## Learning Goals

By completing the project, the following concepts should become practical rather than purely theoretical:

* Cloud service models
* IaaS
* PaaS
* SaaS
* Virtualization
* Containers
* Networking
* Storage
* Resource pooling
* Elasticity
* Scalability
* High availability
* Multitenancy
* Monitoring
* Infrastructure management

---

## Status

**Current stage:** Local FastAPI + PostgreSQL application

**Next stage:** Docker containerization

```text
[✓] FastAPI application
[✓] PostgreSQL integration
[✓] SQLAlchemy models
[✓] REST API
[✓] Environment configuration
[ ] Virtual machine deployment
[ ] Docker
[ ] Nginx
[ ] Load balancing
[ ] Cloud deployment
[ ] Monitoring
[ ] Kubernetes
```

---

## License

This project is intended for educational and cloud-computing infrastructure practice.
