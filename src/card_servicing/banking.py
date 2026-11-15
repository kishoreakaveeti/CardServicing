"""Customer banking domain and HTTP adapter; credentials never enter agent tools."""
from contextlib import closing
import hashlib
import hmac
import secrets
import sqlite3
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from card_servicing.models import Card, CardCreate, CardIssuance
from card_servicing.ports import AccountNotOwned, BlockedCardExists
from card_servicing.idempotency import IdempotencyConflict
from card_servicing.persistence import storage_operation
from card_chat.security import principal


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_.-]{2,63}$')
    password: str = Field(min_length=10, max_length=128, repr=False,
                          json_schema_extra={'writeOnly': True, 'format': 'password'})


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_type: str = Field(pattern=r'^(savings|transaction)$')
    issue_card: bool = False


class CardIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_number: str = Field(min_length=1, max_length=64)
    additional_card: bool = Field(default=False, strict=True,
        description='Explicitly issue another card even when an account already has one. Requires customer confirmation.')


SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
 account_number TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
 account_type TEXT NOT NULL CHECK(account_type IN ('savings','transaction')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_accounts_customer ON accounts(customer_id);
CREATE TABLE IF NOT EXISTS account_cards (
 card_id TEXT PRIMARY KEY REFERENCES cards(id),
 account_number TEXT NOT NULL REFERENCES accounts(account_number));
CREATE TABLE IF NOT EXISTS banking_requests (
 customer_id TEXT NOT NULL REFERENCES customers(id), request_key TEXT NOT NULL,
 fingerprint TEXT NOT NULL, account_number TEXT NOT NULL REFERENCES accounts(account_number),
 PRIMARY KEY(customer_id, request_key));
"""


class BankingService:
    def __init__(self, store):
        self.store = store
        self.lock = RLock()

    @storage_operation
    def register(self, body):
        salt = secrets.token_hex(16)
        digest = hashlib.scrypt(body.password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        customer = {'sub': str(uuid4()), 'preferred_username': body.username.lower(), 'accounts': []}
        try:
            with closing(self.store.connect()) as db, db:
                db.execute('INSERT INTO customers VALUES(?,?,?)',
                           (customer['sub'], customer['preferred_username'], 'scrypt$' + salt + '$' + digest))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Username is already registered') from None
        return customer

    @storage_operation
    def login(self, body):
        with closing(self.store.connect()) as db:
            row = db.execute('SELECT id,username,password_hash FROM customers WHERE username=?', (body.username,)).fetchone()
        # Equal expensive work for unknown users; never log passwords or hashes.
        encoded = row[2] if row else 'scrypt$' + '00' * 16 + '$' + '00' * 64
        _, salt, expected = encoded.split('$')
        actual = hashlib.scrypt(body.password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        if not hmac.compare_digest(actual, expected) or row is None:
            raise HTTPException(401, 'Invalid username or password')
        return {'sub': row[0], 'preferred_username': row[1],
                'accounts': [a['account_number'] for a in self.accounts(row[0])]}

    @storage_operation
    def accounts(self, customer):
        with closing(self.store.connect()) as db:
            rows = db.execute('SELECT account_number,account_type,created_at FROM accounts WHERE customer_id=? ORDER BY created_at,account_number', (customer,)).fetchall()
        return [dict(zip(('account_number', 'account_type', 'created_at'), row)) for row in rows]

    @storage_operation
    def owns(self, customer, account):
        with closing(self.store.connect()) as db:
            return db.execute('SELECT 1 FROM accounts WHERE customer_id=? AND account_number=?', (customer, account)).fetchone() is not None

    @storage_operation
    def registered(self, customer):
        with closing(self.store.connect()) as db:
            return db.execute('SELECT 1 FROM customers WHERE id=?', (customer,)).fetchone() is not None

    @storage_operation
    def managed_account(self, number):
        with closing(self.store.connect()) as db:
            return db.execute('SELECT 1 FROM accounts WHERE account_number=?', (number,)).fetchone() is not None

    @storage_operation
    def create_account(self, customer, body, key):
        identity = body.model_dump_json()
        with self.lock, closing(self.store.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM customers WHERE id=?', (customer['sub'],)).fetchone():
                raise HTTPException(403, 'Register and log in to create accounts')
            old = db.execute('SELECT fingerprint,account_number FROM banking_requests WHERE customer_id=? AND request_key=?', (customer['sub'], key)).fetchone()
            if old and old[0] != identity:
                raise HTTPException(409, 'Request key already used for different details')
            number = old[1] if old else 'ACC-' + uuid4().hex
            if not old:
                db.execute('INSERT INTO accounts(account_number,customer_id,account_type) VALUES(?,?,?)', (number, customer['sub'], body.account_type))
                db.execute('INSERT INTO banking_requests VALUES(?,?,?,?)', (customer['sub'], key, identity, number))
        # Separate durable card operation: retrying this same account key completes any interrupted issuance.
        if body.issue_card:
            self.issue(customer, number, 'account:' + key)
        return next(a for a in self.accounts(customer['sub']) if a['account_number'] == number)

    @storage_operation
    def issue(self, customer, number, key, *, additional=False, with_replay=False):
        if not self.owns(customer['sub'], number):
            raise HTTPException(404, 'Account not found')
        try:
            result, replayed = self.store.issue_idempotent(CardCreate(user_name=customer['username'],
                given_name=customer['username'], account_number=number), customer['sub'] + ':' + key,
                additional=additional, customer_id=customer['sub'])
        except AccountNotOwned:
            raise HTTPException(404, 'Account not found') from None
        except BlockedCardExists:
            raise HTTPException(400, 'Cannot issue a new card while an existing card for this account is blocked.') from None
        return (result, replayed) if with_replay else result

    @storage_operation
    def block_card(self, customer, card_id, reason='Customer requested'):
        card = self.store.get_card(card_id)
        if not card:
            raise HTTPException(404, 'Card not found')
        if customer and not self.owns(customer['sub'], card.account_number):
            raise HTTPException(403, 'Card does not belong to authorized customer')
        self.store.update_card_status(card.id, 'blocked')
        return {'card_id': str(card.id), 'status': 'blocked', 'message': 'Card successfully blocked'}

    @storage_operation
    def unblock_card(self, customer, card_id, reason='Customer requested unblock'):
        card = self.store.get_card(card_id)
        if not card:
            raise HTTPException(404, 'Card not found')
        if customer and not self.owns(customer['sub'], card.account_number):
            raise HTTPException(403, 'Card does not belong to authorized customer')
        if card.status != 'blocked':
            raise HTTPException(400, 'Only blocked cards can be unblocked')
        self.store.update_card_status(card.id, 'created')
        return {'card_id': str(card.id), 'status': 'created', 'message': 'Card successfully unblocked'}

    @storage_operation
    def cards(self, customer):
        with closing(self.store.connect()) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT c.* FROM cards c JOIN accounts a ON a.account_number=c.account_number WHERE a.customer_id=? ORDER BY c.created_at', (customer,)).fetchall()
        return [Card.model_validate(dict(row)) for row in rows]


router = APIRouter(prefix='/banking', tags=['banking'])


def service(request):
    value = getattr(request.app.state, 'banking', None)
    if value is None:
        raise HTTPException(503, 'Banking storage unavailable')
    return value


def customer():
    value = principal.get()
    if not value:
        raise HTTPException(401, 'Sign in required')
    return value


def diagnostic(request, event, card=None):
    fields = request.app.state.telemetry.account_fields(card) if card else {}
    request.app.state.telemetry.emit('log', event, service='cards', level='INFO',
        trace_id=getattr(request.state, 'trace_id', None),
        **({'card_id': str(card.id), 'status': card.status} if card else {}), **fields)


@router.post('/register', status_code=201)
def register(body: Credentials, request: Request):
    result = service(request).register(body)
    diagnostic(request, 'customer_registered')
    return result


@router.post('/login')
def login(body: Credentials, request: Request):
    result = service(request).login(body)
    diagnostic(request, 'customer_authenticated')
    return result


class Account(BaseModel):
    account_number: str
    account_type: str
    created_at: str


class Accounts(BaseModel):
    accounts: list[Account]
    message: str


class Cards(BaseModel):
    cards: list[Card]
    message: str


@router.get('/accounts', operation_id='view_accounts', response_model=Accounts)
def accounts(request: Request):
    result = service(request).accounts(customer()['sub'])
    return {'accounts': result, 'message': 'Your accounts are shown below.' if result else 'No accounts yet. Choose Create account to get started.'}


@router.post('/accounts', operation_id='create_account', response_model=Account, status_code=201)
def create_account(body: AccountCreate, request: Request, idempotency_key: str = Header(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')):
    result = service(request).create_account(customer(), body, idempotency_key)
    diagnostic(request, 'account_creation_completed')
    return result


@router.get('/cards', operation_id='view_cards', response_model=Cards)
def cards(request: Request):
    result = service(request).cards(customer()['sub'])
    return {'cards': result, 'message': 'Your simulated cards are shown below.' if result else 'No cards yet. Create an account or choose Issue new card.'}


@router.post('/cards', operation_id='issue_card', response_model=CardIssuance, status_code=201,
             responses={200: {'model': CardIssuance, 'description': 'An existing card was returned; no new card was created'}})
def issue_card(body: CardIssue, request: Request, response: Response, idempotency_key: str = Header(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')):
    try:
        result, replayed = service(request).issue(customer(), body.account_number, idempotency_key,
            additional=body.additional_card, with_replay=True)
        response.status_code = 200 if result.outcome == 'already_exists' else 201
        response.headers['Idempotency-Replayed'] = str(replayed).lower()
        response.headers['Card-Issuance-Outcome'] = result.outcome
        diagnostic(request, 'card_replayed' if replayed else 'card_' + result.outcome, result)
        return result
    except IdempotencyConflict:
        raise HTTPException(409, 'Request key already used for different details') from None


class CardBlockInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(default="Customer requested", min_length=1, max_length=200, title="Reason")


class CardBlockResponse(BaseModel):
    card_id: str = Field(title="Card Id")
    status: str = Field(title="Status")
    message: str = Field(default="Card successfully blocked", title="Message")


@router.post('/cards/{card_id}/block', operation_id='block_card', response_model=CardBlockResponse)
def block_card(card_id: str, body: CardBlockInput, request: Request):
    cust = customer()
    result = service(request).block_card(cust, card_id, body.reason)
    diagnostic(request, 'card_blocked')
    return result


@router.post('/cards/{card_id}/unblock', operation_id='unblock_card', response_model=CardBlockResponse)
def unblock_card(card_id: str, body: CardBlockInput, request: Request):
    cust = customer()
    result = service(request).unblock_card(cust, card_id, body.reason)
    diagnostic(request, 'card_unblocked')
    return result
