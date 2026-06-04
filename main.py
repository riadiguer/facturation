from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
import json
from datetime import datetime
import os
import hashlib
import hmac
import secrets
import time

from starlette.middleware.sessions import SessionMiddleware

app = FastAPI(title="Facturation - EURL E-BUSINESS LAB")
app.add_middleware(SessionMiddleware, secret_key="ebl-facturation-2026-xK9mP3qR")

templates = Jinja2Templates(directory="templates")

_ITERATIONS = 260_000

# ── Backend detection ────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    IntegrityError = psycopg2.IntegrityError
else:
    import sqlite3
    IntegrityError = sqlite3.IntegrityError
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facturation.db")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key  = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2:{salt}:{key.hex()}"

def verify_password(plain: str, stored: str) -> bool:
    try:
        _, salt, key_hex = stored.split(":", 2)
        check = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), _ITERATIONS)
        return hmac.compare_digest(check.hex(), key_hex)
    except Exception:
        return False


# ── DB wrapper ───────────────────────────────────────────────────────────────

class _DB:
    def __init__(self, conn):
        self._conn = conn
        self._cur  = None

    def execute(self, sql: str, params=()):
        if USE_POSTGRES:
            self._cur = self._conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            )
            pg_sql = sql.replace("?", "%s")
            self._cur.execute(pg_sql, params) if params else self._cur.execute(pg_sql)
        else:
            self._cur = self._conn.execute(sql, params)
        return self

    def insert(self, sql: str, params=()):
        """Execute an INSERT; appends RETURNING id for PostgreSQL."""
        if USE_POSTGRES:
            return self.execute(sql + " RETURNING id", params)
        return self.execute(sql, params)

    def _to_dict(self, row) -> dict:
        d = dict(row)
        return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in d.items()}

    def fetchone(self):
        row = self._cur.fetchone()
        return self._to_dict(row) if row else None

    def fetchall(self):
        return [self._to_dict(r) for r in self._cur.fetchall()]

    @property
    def lastrowid(self):
        if USE_POSTGRES:
            row = self._cur.fetchone()
            return dict(row)["id"] if row else None
        return self._cur.lastrowid

    def commit(self):   self._conn.commit()
    def rollback(self): self._conn.rollback()
    def close(self):    self._conn.close()


def get_db() -> _DB:
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    return _DB(conn)


# ── Database init ────────────────────────────────────────────────────────────

def init_db():
    conn = get_db()

    if USE_POSTGRES:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id              SERIAL PRIMARY KEY,
                facture_num     TEXT UNIQUE NOT NULL,
                facture_date    TEXT,
                client_doit     TEXT,
                client_adresse  TEXT,
                client_ai       TEXT,
                client_rc       TEXT,
                client_nif      TEXT,
                client_nis      TEXT,
                charge          TEXT,
                secteur         TEXT,
                mode_reglement  TEXT,
                services        TEXT    DEFAULT '[]',
                montant_ht      REAL    DEFAULT 0,
                montant_tva     REAL    DEFAULT 0,
                montant_ttc     REAL    DEFAULT 0,
                timbre          REAL    DEFAULT 0,
                net_a_payer     REAL    DEFAULT 0,
                montant_lettre  TEXT,
                created_by      TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS proformas (
                id                SERIAL PRIMARY KEY,
                proforma_num      TEXT UNIQUE NOT NULL,
                proforma_date     TEXT,
                client_code       TEXT,
                client_raison     TEXT,
                client_nom        TEXT,
                client_adresse    TEXT,
                client_rc         TEXT,
                client_nif        TEXT,
                client_nis        TEXT,
                client_ai         TEXT,
                client_email      TEXT,
                client_tel        TEXT,
                lignes            TEXT  DEFAULT '[]',
                total_ht          REAL  DEFAULT 0,
                remise_pct        REAL  DEFAULT 0,
                remise_montant    REAL  DEFAULT 0,
                montant_tva       REAL  DEFAULT 0,
                total_ttc         REAL  DEFAULT 0,
                objet             TEXT,
                reglement         TEXT  DEFAULT 'Chèque',
                paiement          TEXT  DEFAULT '40%% à la commande, 30%% à mi-projet, 30%% à la livraison',
                validite_jours    INTEGER,
                delai_min         INTEGER,
                delai_max         INTEGER,
                created_by        TEXT,
                created_at        TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devis (
                id                SERIAL PRIMARY KEY,
                devis_num         TEXT UNIQUE NOT NULL,
                devis_date        TEXT,
                client_code       TEXT,
                client_raison     TEXT,
                client_nom        TEXT,
                client_adresse    TEXT,
                client_rc         TEXT,
                client_nif        TEXT,
                client_nis        TEXT,
                client_ai         TEXT,
                client_email      TEXT,
                client_tel        TEXT,
                lignes            TEXT  DEFAULT '[]',
                total_ht          REAL  DEFAULT 0,
                remise_pct        REAL  DEFAULT 0,
                remise_montant    REAL  DEFAULT 0,
                montant_tva       REAL  DEFAULT 0,
                total_ttc         REAL  DEFAULT 0,
                apply_tva         INTEGER DEFAULT 1,
                objet             TEXT,
                reglement         TEXT  DEFAULT 'Chèque',
                paiement          TEXT  DEFAULT '40%% à la commande, 30%% à mi-projet, 30%% à la livraison',
                validite_jours    INTEGER,
                delai_min         INTEGER,
                delai_max         INTEGER,
                created_by        TEXT,
                created_at        TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bons_commande (
                id                SERIAL PRIMARY KEY,
                bc_num            TEXT UNIQUE NOT NULL,
                bc_date           TEXT,
                client_adresse_a  TEXT,
                client_nom        TEXT,
                client_rc         TEXT,
                client_nif        TEXT,
                client_nis        TEXT,
                client_art        TEXT,
                client_tel        TEXT,
                client_adresse    TEXT,
                lignes            TEXT  DEFAULT '[]',
                montant_ht        REAL  DEFAULT 0,
                montant_tva       REAL  DEFAULT 0,
                montant_ttc       REAL  DEFAULT 0,
                montant_lettre    TEXT,
                mode_reglement    TEXT,
                created_by        TEXT,
                created_at        TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bons_versement (
                id                SERIAL PRIMARY KEY,
                bv_num            TEXT UNIQUE NOT NULL,
                bv_date           TEXT,
                client_adresse_a  TEXT,
                client_nom        TEXT,
                client_rc         TEXT,
                client_nif        TEXT,
                client_nis        TEXT,
                client_art        TEXT,
                client_tel        TEXT,
                client_adresse    TEXT,
                lignes            TEXT  DEFAULT '[]',
                montant_ht        REAL  DEFAULT 0,
                montant_tva       REAL  DEFAULT 0,
                montant_ttc       REAL  DEFAULT 0,
                montant_lettre    TEXT,
                mode_reglement    TEXT,
                created_by        TEXT,
                created_at        TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'agent'
                              CHECK(role IN ('admin', 'agent'))
            )
        """)
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?) ON CONFLICT DO NOTHING",
            ("admin", hash_password("Admin@123"), "admin")
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?) ON CONFLICT DO NOTHING",
            ("agent", hash_password("Agent@123"), "agent")
        )

    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                facture_num     TEXT UNIQUE NOT NULL,
                facture_date    TEXT,
                client_doit     TEXT,
                client_adresse  TEXT,
                client_ai       TEXT,
                client_rc       TEXT,
                client_nif      TEXT,
                client_nis      TEXT,
                charge          TEXT,
                secteur         TEXT,
                mode_reglement  TEXT,
                services        TEXT    DEFAULT '[]',
                montant_ht      REAL    DEFAULT 0,
                montant_tva     REAL    DEFAULT 0,
                montant_ttc     REAL    DEFAULT 0,
                timbre          REAL    DEFAULT 0,
                net_a_payer     REAL    DEFAULT 0,
                montant_lettre  TEXT,
                created_by      TEXT,
                created_at      TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS proformas (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                proforma_num      TEXT UNIQUE NOT NULL,
                proforma_date     TEXT,
                client_code       TEXT,
                client_raison     TEXT,
                client_nom        TEXT,
                client_adresse    TEXT,
                client_rc         TEXT,
                client_nif        TEXT,
                client_nis        TEXT,
                client_ai         TEXT,
                client_email      TEXT,
                client_tel        TEXT,
                lignes            TEXT  DEFAULT '[]',
                total_ht          REAL  DEFAULT 0,
                remise_pct        REAL  DEFAULT 0,
                remise_montant    REAL  DEFAULT 0,
                montant_tva       REAL  DEFAULT 0,
                total_ttc         REAL  DEFAULT 0,
                objet             TEXT,
                reglement         TEXT  DEFAULT 'Chèque',
                paiement          TEXT  DEFAULT '40% à la commande, 30% à mi-projet, 30% à la livraison',
                validite_jours    INTEGER,
                delai_min         INTEGER,
                delai_max         INTEGER,
                created_by        TEXT,
                created_at        TEXT  DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devis (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                devis_num         TEXT UNIQUE NOT NULL,
                devis_date        TEXT,
                client_code       TEXT,
                client_raison     TEXT,
                client_nom        TEXT,
                client_adresse    TEXT,
                client_rc         TEXT,
                client_nif        TEXT,
                client_nis        TEXT,
                client_ai         TEXT,
                client_email      TEXT,
                client_tel        TEXT,
                lignes            TEXT  DEFAULT '[]',
                total_ht          REAL  DEFAULT 0,
                remise_pct        REAL  DEFAULT 0,
                remise_montant    REAL  DEFAULT 0,
                montant_tva       REAL  DEFAULT 0,
                total_ttc         REAL  DEFAULT 0,
                apply_tva         INTEGER DEFAULT 1,
                objet             TEXT,
                reglement         TEXT  DEFAULT 'Chèque',
                paiement          TEXT  DEFAULT '40% à la commande, 30% à mi-projet, 30% à la livraison',
                validite_jours    INTEGER,
                delai_min         INTEGER,
                delai_max         INTEGER,
                created_by        TEXT,
                created_at        TEXT  DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bons_commande (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                bc_num            TEXT UNIQUE NOT NULL,
                bc_date           TEXT,
                client_adresse_a  TEXT,
                client_nom        TEXT,
                client_rc         TEXT,
                client_nif        TEXT,
                client_nis        TEXT,
                client_art        TEXT,
                client_tel        TEXT,
                client_adresse    TEXT,
                lignes            TEXT  DEFAULT '[]',
                montant_ht        REAL  DEFAULT 0,
                montant_tva       REAL  DEFAULT 0,
                montant_ttc       REAL  DEFAULT 0,
                montant_lettre    TEXT,
                mode_reglement    TEXT,
                created_by        TEXT,
                created_at        TEXT  DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bons_versement (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                bv_num            TEXT UNIQUE NOT NULL,
                bv_date           TEXT,
                client_adresse_a  TEXT,
                client_nom        TEXT,
                client_rc         TEXT,
                client_nif        TEXT,
                client_nis        TEXT,
                client_art        TEXT,
                client_tel        TEXT,
                client_adresse    TEXT,
                lignes            TEXT  DEFAULT '[]',
                montant_ht        REAL  DEFAULT 0,
                montant_tva       REAL  DEFAULT 0,
                montant_ttc       REAL  DEFAULT 0,
                montant_lettre    TEXT,
                mode_reglement    TEXT,
                created_by        TEXT,
                created_at        TEXT  DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'agent'
                              CHECK(role IN ('admin', 'agent'))
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?,?,?)",
            ("admin", hash_password("Admin@123"), "admin")
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role) VALUES (?,?,?)",
            ("agent", hash_password("Agent@123"), "agent")
        )

    conn.commit()
    conn.close()


if USE_POSTGRES:
    for _attempt in range(5):
        try:
            init_db()
            break
        except Exception:
            if _attempt == 4:
                raise
            time.sleep(2 ** _attempt)
else:
    init_db()


def migrate_db():
    """Safely add columns introduced after initial schema creation."""
    conn = get_db()
    migrations = [
        ("invoices",  "created_by TEXT"),
        ("proformas", "created_by TEXT"),
        ("devis",     "created_by TEXT"),
        ("devis",     "apply_tva INTEGER DEFAULT 1"),
    ]
    for table, col_def in migrations:
        try:
            if USE_POSTGRES:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_def}")
            else:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            conn.rollback()
    conn.close()


migrate_db()


# ── Number generators ───────────────────────────────────────────────────────

def get_next_proforma_number() -> str:
    year = datetime.now().year
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(proforma_num, 10) AS INTEGER)), 0) AS seq "
        "FROM proformas WHERE proforma_num LIKE ?",
        (f"PRO-{year}-%",)
    ).fetchone()
    conn.close()
    return f"PRO-{year}-{row['seq'] + 1:05d}"


def get_next_devis_number() -> str:
    year = datetime.now().year
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(devis_num, 10) AS INTEGER)), 0) AS seq "
        "FROM devis WHERE devis_num LIKE ?",
        (f"DEV-{year}-%",)
    ).fetchone()
    conn.close()
    return f"DEV-{year}-{row['seq'] + 1:05d}"


def get_next_bv_number() -> str:
    year = datetime.now().year
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(bv_num, 9) AS INTEGER)), 0) AS seq "
        "FROM bons_versement WHERE bv_num LIKE ?",
        (f"BV-{year}-%",)
    ).fetchone()
    conn.close()
    return f"BV-{year}-{row['seq'] + 1:05d}"


def get_next_bc_number() -> str:
    year = datetime.now().year
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(bc_num, 9) AS INTEGER)), 0) AS seq "
        "FROM bons_commande WHERE bc_num LIKE ?",
        (f"BC-{year}-%",)
    ).fetchone()
    conn.close()
    return f"BC-{year}-{row['seq'] + 1:05d}"


def get_next_invoice_number() -> str:
    year = datetime.now().year
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(MAX(CAST(SUBSTR(facture_num, 6) AS INTEGER)), 0) AS seq "
        "FROM invoices WHERE facture_num LIKE ?",
        (f"{year}-%",)
    ).fetchone()
    conn.close()
    return f"{year}-{row['seq'] + 1:05d}"


def session_user(request: Request):
    return request.session.get("user")


# ── Pydantic models ────────────────────────────────────────────────────────

class ServiceRow(BaseModel):
    service: str = ""
    details: str = ""
    prix: float = 0.0


class InvoiceCreate(BaseModel):
    facture_num: str
    facture_date: Optional[str] = None
    client_doit: Optional[str] = None
    client_adresse: Optional[str] = None
    client_ai: Optional[str] = None
    client_rc: Optional[str] = None
    client_nif: Optional[str] = None
    client_nis: Optional[str] = None
    charge: Optional[str] = None
    secteur: Optional[str] = None
    mode_reglement: Optional[str] = None
    services: List[ServiceRow] = []
    montant_ht: float = 0
    montant_tva: float = 0
    montant_ttc: float = 0
    timbre: float = 0
    net_a_payer: float = 0
    montant_lettre: Optional[str] = None


class LigneProforma(BaseModel):
    reference:   str   = ""
    description: str   = ""
    pu_ht:       float = 0.0
    quantite:    float = 0.0
    montant_ht:  float = 0.0


class ProformaCreate(BaseModel):
    proforma_num:   str
    proforma_date:  Optional[str] = None
    client_code:    Optional[str] = None
    client_raison:  Optional[str] = None
    client_nom:     Optional[str] = None
    client_adresse: Optional[str] = None
    client_rc:      Optional[str] = None
    client_nif:     Optional[str] = None
    client_nis:     Optional[str] = None
    client_ai:      Optional[str] = None
    client_email:   Optional[str] = None
    client_tel:     Optional[str] = None
    lignes:         List[LigneProforma] = []
    total_ht:       float = 0
    remise_pct:     float = 0
    remise_montant: float = 0
    montant_tva:    float = 0
    total_ttc:      float = 0
    objet:          Optional[str] = None
    reglement:      Optional[str] = "Chèque"
    paiement:       Optional[str] = "40% à la commande, 30% à mi-projet, 30% à la livraison"
    validite_jours: Optional[int] = None
    delai_min:      Optional[int] = None
    delai_max:      Optional[int] = None


class DevisCreate(BaseModel):
    devis_num:      str
    devis_date:     Optional[str] = None
    client_code:    Optional[str] = None
    client_raison:  Optional[str] = None
    client_nom:     Optional[str] = None
    client_adresse: Optional[str] = None
    client_rc:      Optional[str] = None
    client_nif:     Optional[str] = None
    client_nis:     Optional[str] = None
    client_ai:      Optional[str] = None
    client_email:   Optional[str] = None
    client_tel:     Optional[str] = None
    lignes:         List[LigneProforma] = []
    total_ht:       float = 0
    remise_pct:     float = 0
    remise_montant: float = 0
    montant_tva:    float = 0
    total_ttc:      float = 0
    apply_tva:      bool = True
    objet:          Optional[str] = None
    reglement:      Optional[str] = "Chèque"
    paiement:       Optional[str] = "40% à la commande, 30% à mi-projet, 30% à la livraison"
    validite_jours: Optional[int] = None
    delai_min:      Optional[int] = None
    delai_max:      Optional[int] = None


class LigneBC(BaseModel):
    description:   str   = ""
    quantite:      float = 0.0
    prix_unitaire: float = 0.0
    taxes:         str   = "TVA 19%"
    montant:       float = 0.0


class BonCommandeCreate(BaseModel):
    bc_num:           str
    bc_date:          Optional[str] = None
    client_adresse_a: Optional[str] = None
    client_nom:       Optional[str] = None
    client_rc:        Optional[str] = None
    client_nif:       Optional[str] = None
    client_nis:       Optional[str] = None
    client_art:       Optional[str] = None
    client_tel:       Optional[str] = None
    client_adresse:   Optional[str] = None
    lignes:           List[LigneBC] = []
    montant_ht:       float = 0
    montant_tva:      float = 0
    montant_ttc:      float = 0
    montant_lettre:   Optional[str] = None
    mode_reglement:   Optional[str] = None


class BonVersementCreate(BaseModel):
    bv_num:           str
    bv_date:          Optional[str] = None
    client_adresse_a: Optional[str] = None
    client_nom:       Optional[str] = None
    client_rc:        Optional[str] = None
    client_nif:       Optional[str] = None
    client_nis:       Optional[str] = None
    client_art:       Optional[str] = None
    client_tel:       Optional[str] = None
    client_adresse:   Optional[str] = None
    lignes:           List[LigneBC] = []
    montant_ht:       float = 0
    montant_tva:      float = 0
    montant_ttc:      float = 0
    montant_lettre:   Optional[str] = None
    mode_reglement:   Optional[str] = None


# ── Auth routes ────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if session_user(request):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()

    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(request, "login.html", {
            "error": "Nom d'utilisateur ou mot de passe incorrect."
        })

    request.session["user"] = {
        "id":       user["id"],
        "username": user["username"],
        "role":     user["role"]
    }
    return RedirectResponse(url="/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ── Admin: user management ─────────────────────────────────────────────────

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user["role"] != "admin":
        return RedirectResponse(url="/", status_code=302)

    conn = get_db()
    users = conn.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
    conn.close()

    return templates.TemplateResponse(request, "admin_users.html", {
        "users":        users,
        "current_user": user,
        "success":      request.query_params.get("success"),
        "error":        request.query_params.get("error"),
    })


@app.post("/admin/users/create", response_class=HTMLResponse)
async def create_user(request: Request):
    current = session_user(request)
    if not current:
        return RedirectResponse(url="/login", status_code=302)
    if current["role"] != "admin":
        return RedirectResponse(url="/", status_code=302)

    form = await request.form()
    username = str(form.get("username", "")).strip().lower()
    password = str(form.get("password", "")).strip()
    confirm  = str(form.get("confirm_password", "")).strip()
    role     = str(form.get("role", "agent")).strip()

    if not username or len(username) < 3:
        return RedirectResponse(
            url="/admin/users?error=Le+nom+d%27utilisateur+doit+avoir+au+moins+3+caractères",
            status_code=302
        )
    if role not in ("admin", "agent"):
        return RedirectResponse(url="/admin/users?error=Rôle+invalide", status_code=302)
    if len(password) < 6:
        return RedirectResponse(
            url="/admin/users?error=Le+mot+de+passe+doit+avoir+au+moins+6+caractères",
            status_code=302
        )
    if password != confirm:
        return RedirectResponse(
            url="/admin/users?error=Les+mots+de+passe+ne+correspondent+pas",
            status_code=302
        )

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, hash_password(password), role)
        )
        conn.commit()
        conn.close()
        return RedirectResponse(url="/admin/users?success=created", status_code=302)
    except IntegrityError:
        conn.rollback()
        conn.close()
        return RedirectResponse(
            url="/admin/users?error=Ce+nom+d%27utilisateur+existe+déjà",
            status_code=302
        )


@app.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
async def delete_user(request: Request, user_id: int):
    current = session_user(request)
    if not current:
        return RedirectResponse(url="/login", status_code=302)
    if current["role"] != "admin":
        return RedirectResponse(url="/", status_code=302)
    if current["id"] == user_id:
        return RedirectResponse(
            url="/admin/users?error=Vous+ne+pouvez+pas+supprimer+votre+propre+compte",
            status_code=302
        )
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/users?success=deleted", status_code=302)


@app.post("/admin/users/{user_id}/password", response_class=HTMLResponse)
async def change_password(request: Request, user_id: int):
    current = session_user(request)
    if not current:
        return RedirectResponse(url="/login", status_code=302)
    if current["role"] != "admin":
        return RedirectResponse(url="/", status_code=302)

    form = await request.form()
    new_pw  = str(form.get("new_password", "")).strip()
    confirm = str(form.get("confirm_password", "")).strip()

    if len(new_pw) < 6:
        return RedirectResponse(
            url="/admin/users?error=Le+mot+de+passe+doit+avoir+au+moins+6+caractères",
            status_code=302
        )
    if new_pw != confirm:
        return RedirectResponse(
            url="/admin/users?error=Les+mots+de+passe+ne+correspondent+pas",
            status_code=302
        )

    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new_pw), user_id)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/users?success=1", status_code=302)


# ── App routes ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    conn = get_db()
    rows = conn.execute(
        "SELECT id, facture_num, facture_date, client_doit, "
        "montant_ht, net_a_payer, created_by, created_at "
        "FROM invoices ORDER BY id DESC"
    ).fetchall()
    proforma_rows = conn.execute(
        "SELECT id, proforma_num, proforma_date, client_raison, "
        "total_ht, total_ttc, created_by, created_at "
        "FROM proformas ORDER BY id DESC"
    ).fetchall()
    devis_rows = conn.execute(
        "SELECT id, devis_num, devis_date, client_raison, "
        "total_ht, total_ttc, created_by, created_at "
        "FROM devis ORDER BY id DESC"
    ).fetchall()
    bc_rows = conn.execute(
        "SELECT id, bc_num, bc_date, client_nom, "
        "montant_ht, montant_ttc, created_by, created_at "
        "FROM bons_commande ORDER BY id DESC"
    ).fetchall()
    bv_rows = conn.execute(
        "SELECT id, bv_num, bv_date, client_nom, "
        "montant_ht, montant_ttc, created_by, created_at "
        "FROM bons_versement ORDER BY id DESC"
    ).fetchall()
    conn.close()

    return templates.TemplateResponse(request, "index.html", {
        "invoices":        rows,
        "proformas":       proforma_rows,
        "devis_list":      devis_rows,
        "bons_commande":   bc_rows,
        "bons_versement":  bv_rows,
        "current_user":    user
    })


@app.get("/invoice/new", response_class=HTMLResponse)
async def new_invoice(request: Request):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "invoice.html", {
        "facture_num":  get_next_invoice_number(),
        "invoice":      None,
        "readonly":     False,
        "creator":      None,
        "current_user": user
    })


@app.get("/invoice/{invoice_id}", response_class=HTMLResponse)
async def view_invoice(request: Request, invoice_id: int):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    inv = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    conn.close()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture introuvable")
    inv["services"] = json.loads(inv.get("services") or "[]")
    return templates.TemplateResponse(request, "invoice.html", {
        "facture_num":  inv["facture_num"],
        "invoice":      inv,
        "readonly":     True,
        "creator":      inv.get("created_by") or "—",
        "current_user": user
    })


@app.get("/proforma/new", response_class=HTMLResponse)
async def new_proforma(request: Request):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "proforma.html", {
        "proforma_num": get_next_proforma_number(),
        "proforma":     None,
        "readonly":     False,
        "creator":      None,
        "current_user": user
    })


@app.get("/proforma/{proforma_id}", response_class=HTMLResponse)
async def view_proforma(request: Request, proforma_id: int):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    pro = conn.execute("SELECT * FROM proformas WHERE id = ?", (proforma_id,)).fetchone()
    conn.close()
    if not pro:
        raise HTTPException(status_code=404, detail="Proforma introuvable")
    pro["lignes"] = json.loads(pro.get("lignes") or "[]")
    return templates.TemplateResponse(request, "proforma.html", {
        "proforma_num": pro["proforma_num"],
        "proforma":     pro,
        "readonly":     True,
        "creator":      pro.get("created_by") or "—",
        "current_user": user
    })


@app.post("/api/proformas")
async def create_proforma(request: Request, data: ProformaCreate):
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non authentifié")
    conn = get_db()
    try:
        conn.insert("""
            INSERT INTO proformas
            (proforma_num, proforma_date,
             client_code, client_raison, client_nom, client_adresse,
             client_rc, client_nif, client_nis, client_ai, client_email, client_tel,
             lignes, total_ht, remise_pct, remise_montant, montant_tva, total_ttc,
             objet, reglement, paiement, validite_jours, delai_min, delai_max,
             created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.proforma_num, data.proforma_date,
            data.client_code, data.client_raison, data.client_nom, data.client_adresse,
            data.client_rc, data.client_nif, data.client_nis, data.client_ai,
            data.client_email, data.client_tel,
            json.dumps([l.model_dump() for l in data.lignes]),
            data.total_ht, data.remise_pct, data.remise_montant,
            data.montant_tva, data.total_ttc,
            data.objet, data.reglement, data.paiement,
            data.validite_jours, data.delai_min, data.delai_max,
            user["username"]
        ))
        new_id = conn.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "id": new_id, "proforma_num": data.proforma_num}
    except IntegrityError:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=409, detail=f"Le proforma {data.proforma_num} existe déjà")


@app.get("/devis/new", response_class=HTMLResponse)
async def new_devis(request: Request):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "devis.html", {
        "devis_num":    get_next_devis_number(),
        "devis":        None,
        "readonly":     False,
        "creator":      None,
        "current_user": user
    })


@app.get("/devis/{devis_id}", response_class=HTMLResponse)
async def view_devis(request: Request, devis_id: int):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    dev = conn.execute("SELECT * FROM devis WHERE id = ?", (devis_id,)).fetchone()
    conn.close()
    if not dev:
        raise HTTPException(status_code=404, detail="Devis introuvable")
    dev["lignes"] = json.loads(dev.get("lignes") or "[]")
    auto_edit = request.query_params.get("edit") == "1" and user["role"] == "admin"
    return templates.TemplateResponse(request, "devis.html", {
        "devis_num":    dev["devis_num"],
        "devis":        dev,
        "readonly":     True,
        "auto_edit":    auto_edit,
        "creator":      dev.get("created_by") or "—",
        "current_user": user
    })


@app.post("/api/devis")
async def create_devis(request: Request, data: DevisCreate):
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non authentifié")
    conn = get_db()
    try:
        conn.insert("""
            INSERT INTO devis
            (devis_num, devis_date,
             client_code, client_raison, client_nom, client_adresse,
             client_rc, client_nif, client_nis, client_ai, client_email, client_tel,
             lignes, total_ht, remise_pct, remise_montant, montant_tva, total_ttc,
             apply_tva, objet, reglement, paiement, validite_jours, delai_min, delai_max,
             created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.devis_num, data.devis_date,
            data.client_code, data.client_raison, data.client_nom, data.client_adresse,
            data.client_rc, data.client_nif, data.client_nis, data.client_ai,
            data.client_email, data.client_tel,
            json.dumps([l.model_dump() for l in data.lignes]),
            data.total_ht, data.remise_pct, data.remise_montant,
            data.montant_tva, data.total_ttc,
            1 if data.apply_tva else 0,
            data.objet, data.reglement, data.paiement,
            data.validite_jours, data.delai_min, data.delai_max,
            user["username"]
        ))
        new_id = conn.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "id": new_id, "devis_num": data.devis_num}
    except IntegrityError:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=409, detail=f"Le devis {data.devis_num} existe déjà")


@app.put("/api/devis/{devis_id}")
async def update_devis(request: Request, devis_id: int, data: DevisCreate):
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non authentifié")
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    conn = get_db()
    existing = conn.execute("SELECT id FROM devis WHERE id = ?", (devis_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Devis introuvable")
    conn.execute("""
        UPDATE devis SET
            devis_date=?, client_code=?, client_raison=?, client_nom=?, client_adresse=?,
            client_rc=?, client_nif=?, client_nis=?, client_ai=?, client_email=?, client_tel=?,
            lignes=?, total_ht=?, remise_pct=?, remise_montant=?, montant_tva=?, total_ttc=?,
            apply_tva=?, objet=?, reglement=?, paiement=?, validite_jours=?, delai_min=?, delai_max=?
        WHERE id=?
    """, (
        data.devis_date,
        data.client_code, data.client_raison, data.client_nom, data.client_adresse,
        data.client_rc, data.client_nif, data.client_nis, data.client_ai,
        data.client_email, data.client_tel,
        json.dumps([l.model_dump() for l in data.lignes]),
        data.total_ht, data.remise_pct, data.remise_montant,
        data.montant_tva, data.total_ttc,
        1 if data.apply_tva else 0,
        data.objet, data.reglement, data.paiement,
        data.validite_jours, data.delai_min, data.delai_max,
        devis_id
    ))
    conn.commit()
    conn.close()
    return {"success": True, "id": devis_id}


@app.get("/bc/new", response_class=HTMLResponse)
async def new_bc(request: Request):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "bon_commande.html", {
        "bc_num":       get_next_bc_number(),
        "bc":           None,
        "readonly":     False,
        "creator":      None,
        "current_user": user
    })


@app.get("/bc/{bc_id}", response_class=HTMLResponse)
async def view_bc(request: Request, bc_id: int):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    bc = conn.execute("SELECT * FROM bons_commande WHERE id = ?", (bc_id,)).fetchone()
    conn.close()
    if not bc:
        raise HTTPException(status_code=404, detail="Bon de commande introuvable")
    bc["lignes"] = json.loads(bc.get("lignes") or "[]")
    return templates.TemplateResponse(request, "bon_commande.html", {
        "bc_num":       bc["bc_num"],
        "bc":           bc,
        "readonly":     True,
        "creator":      bc.get("created_by") or "—",
        "current_user": user
    })


@app.post("/api/bons-commande")
async def create_bc(request: Request, data: BonCommandeCreate):
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non authentifié")
    conn = get_db()
    try:
        conn.insert("""
            INSERT INTO bons_commande
            (bc_num, bc_date,
             client_adresse_a, client_nom, client_rc, client_nif,
             client_nis, client_art, client_tel, client_adresse,
             lignes, montant_ht, montant_tva, montant_ttc,
             montant_lettre, mode_reglement, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.bc_num, data.bc_date,
            data.client_adresse_a, data.client_nom, data.client_rc, data.client_nif,
            data.client_nis, data.client_art, data.client_tel, data.client_adresse,
            json.dumps([l.model_dump() for l in data.lignes]),
            data.montant_ht, data.montant_tva, data.montant_ttc,
            data.montant_lettre, data.mode_reglement,
            user["username"]
        ))
        new_id = conn.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "id": new_id, "bc_num": data.bc_num}
    except IntegrityError:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=409, detail=f"Le bon de commande {data.bc_num} existe déjà")


@app.get("/bv/new", response_class=HTMLResponse)
async def new_bv(request: Request):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request, "bon_versement.html", {
        "bv_num":       get_next_bv_number(),
        "bv":           None,
        "readonly":     False,
        "creator":      None,
        "current_user": user
    })


@app.get("/bv/{bv_id}", response_class=HTMLResponse)
async def view_bv(request: Request, bv_id: int):
    user = session_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    bv = conn.execute("SELECT * FROM bons_versement WHERE id = ?", (bv_id,)).fetchone()
    conn.close()
    if not bv:
        raise HTTPException(status_code=404, detail="Bon de versement introuvable")
    bv["lignes"] = json.loads(bv.get("lignes") or "[]")
    return templates.TemplateResponse(request, "bon_versement.html", {
        "bv_num":       bv["bv_num"],
        "bv":           bv,
        "readonly":     True,
        "creator":      bv.get("created_by") or "—",
        "current_user": user
    })


@app.post("/api/bons-versement")
async def create_bv(request: Request, data: BonVersementCreate):
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non authentifié")
    conn = get_db()
    try:
        conn.insert("""
            INSERT INTO bons_versement
            (bv_num, bv_date,
             client_adresse_a, client_nom, client_rc, client_nif,
             client_nis, client_art, client_tel, client_adresse,
             lignes, montant_ht, montant_tva, montant_ttc,
             montant_lettre, mode_reglement, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.bv_num, data.bv_date,
            data.client_adresse_a, data.client_nom, data.client_rc, data.client_nif,
            data.client_nis, data.client_art, data.client_tel, data.client_adresse,
            json.dumps([l.model_dump() for l in data.lignes]),
            data.montant_ht, data.montant_tva, data.montant_ttc,
            data.montant_lettre, data.mode_reglement,
            user["username"]
        ))
        new_id = conn.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "id": new_id, "bv_num": data.bv_num}
    except IntegrityError:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=409, detail=f"Le bon de versement {data.bv_num} existe déjà")


@app.post("/api/invoices")
async def create_invoice(request: Request, data: InvoiceCreate):
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Non authentifié")
    conn = get_db()
    try:
        conn.insert("""
            INSERT INTO invoices
            (facture_num, facture_date, client_doit, client_adresse, client_ai,
             client_rc, client_nif, client_nis, charge, secteur, mode_reglement,
             services, montant_ht, montant_tva, montant_ttc, timbre, net_a_payer,
             montant_lettre, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.facture_num, data.facture_date, data.client_doit, data.client_adresse,
            data.client_ai, data.client_rc, data.client_nif, data.client_nis,
            data.charge, data.secteur, data.mode_reglement,
            json.dumps([s.model_dump() for s in data.services]),
            data.montant_ht, data.montant_tva, data.montant_ttc,
            data.timbre, data.net_a_payer, data.montant_lettre,
            user["username"]
        ))
        new_id = conn.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "id": new_id, "facture_num": data.facture_num}
    except IntegrityError:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=409, detail=f"La facture {data.facture_num} existe déjà")
