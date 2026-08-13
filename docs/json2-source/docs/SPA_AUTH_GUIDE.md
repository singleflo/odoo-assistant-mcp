# Odoo Authentication API for React SPA

## Overview

This guide covers authentication endpoints for a Vite + React SPA connecting to Odoo 18 backend.

**Base URL**: `http://dev8070:8070` (development)  
**Database**: `luxurent_dev18`

---

## Table of Contents

1. [Login](#1-login)
2. [Get Session Info](#2-get-session-info)
3. [Signup (Register)](#3-signup-register-new-portal-user)
4. [Logout](#4-logout)
5. [Get User Profile](#5-get-current-user-profile)
6. [Query Data](#6-query-data-authenticated-user)
7. [Change Password](#7-change-password-logged-user)
8. [Reset Password (Forgot)](#8-reset-password-forgot-password)
9. [List Databases](#9-list-available-databases)
10. [React Implementation](#react-implementation-example)
11. [CORS Configuration](#cors-configuration)
12. [Error Handling](#error-handling)

---

## Configuration Status

✅ **Public Registration**: Enabled (`b2c` mode)  
✅ **Portal Users**: Auto-created on signup  
✅ **Session Cookies**: Handled automatically

---

## API Endpoints

### 1. Login

**Endpoint**: `POST /web/session/authenticate`

**Headers**:

```
Content-Type: application/json
```

**Request Body**:

```json
{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {
        "db": "luxurent_dev18",
        "login": "user@example.com",
        "password": "password123"
    },
    "id": 1
}
```

**Success Response** (HTTP 200):

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "uid": 38,
        "name": "User Name",
        "username": "user@example.com",
        "is_admin": false,
        "is_internal_user": false,
        "user_context": {
            "lang": "it_IT",
            "tz": "Europe/Rome",
            "uid": 38
        },
        "db": "luxurent_dev18"
    }
}
```

**Error Response** (wrong credentials):

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "error": {
        "code": 200,
        "message": "Odoo Server Error",
        "data": {
            "name": "odoo.exceptions.AccessDenied",
            "message": "Wrong login/password"
        }
    }
}
```

#### JavaScript Implementation

```typescript
interface LoginResult {
    uid: number;
    name: string;
    username: string;
    is_admin: boolean;
    is_internal_user: boolean;
}

async function login(email: string, password: string): Promise<LoginResult> {
    const response = await fetch(`${ODOO_URL}/web/session/authenticate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include", // IMPORTANT: enables cookies
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: {
                db: "luxurent_dev18",
                login: email,
                password: password,
            },
            id: Date.now(),
        }),
    });

    const data = await response.json();

    if (data.error) {
        throw new Error(data.error.data?.message || "Login failed");
    }

    return data.result;
}
```

**Important**: Use `credentials: 'include'` to handle session cookies automatically.

---

### 2. Get Session Info

**Endpoint**: `POST /web/session/get_session_info`

**Headers**:

```
Content-Type: application/json
Cookie: session_id=<your_session_cookie>
```

**Request Body**:

```json
{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {},
    "id": 1
}
```

**Success Response**:

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "uid": 38,
        "username": "user@example.com",
        "name": "User Name",
        "partner_id": 7968,
        "is_admin": false,
        "is_internal_user": false,
        "db": "luxurent_dev18",
        "user_context": {
            "lang": "it_IT",
            "tz": "Europe/Rome"
        }
    }
}
```

**Note**: `partner_id` is useful for querying partner details (address, phone, etc.)

**Expired Session**:

```json
{
    "jsonrpc": "2.0",
    "error": {
        "code": 100,
        "message": "Odoo Session Expired"
    }
}
```

#### JavaScript Implementation

```typescript
interface SessionInfo {
    uid: number | false;
    username: string;
    name: string;
    is_admin: boolean;
    is_internal_user: boolean;
}

async function getSession(): Promise<SessionInfo | null> {
    try {
        const response = await fetch(`${ODOO_URL}/web/session/get_session_info`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            body: JSON.stringify({
                jsonrpc: "2.0",
                method: "call",
                params: {},
                id: Date.now(),
            }),
        });

        const data = await response.json();

        if (data.error?.code === 100) {
            return null; // Session expired
        }

        if (data.result?.uid) {
            return data.result;
        }

        return null;
    } catch {
        return null;
    }
}

// Check if user is authenticated
async function isAuthenticated(): Promise<boolean> {
    const session = await getSession();
    return session !== null && session.uid !== false;
}
```

---

### 3. Signup (Register New Portal User)

Public registration is **enabled**. New users are created as **Portal users**.

#### Step 1: Get CSRF Token

**Endpoint**: `GET /web/signup`

Parse the HTML response to extract the CSRF token:

```html
<input type="hidden" name="csrf_token" value="abc123..." />
```

#### Step 2: Submit Registration

**Endpoint**: `POST /web/signup`

**Headers**:

```
Content-Type: application/x-www-form-urlencoded
Cookie: <session_cookie_from_step_1>
```

**Request Body**:

```
csrf_token=<token>&login=user@example.com&name=User+Name&password=Pass123!&confirm_password=Pass123!
```

**Success**: HTTP 303 redirect to `/my` (user is logged in)

**Error**: HTTP 200 with error message in HTML

#### JavaScript Implementation

```typescript
async function signup(email: string, name: string, password: string): Promise<boolean> {
    // Step 1: Get CSRF token
    const signupPage = await fetch(`${ODOO_URL}/web/signup`, {
        credentials: "include",
    });
    const html = await signupPage.text();
    const csrfMatch = html.match(/name="csrf_token"\s+value="([^"]+)"/);
    if (!csrfMatch) throw new Error("CSRF token not found");

    // Step 2: Submit form
    const formData = new URLSearchParams({
        csrf_token: csrfMatch[1],
        login: email,
        name: name,
        password: password,
        confirm_password: password,
    });

    const response = await fetch(`${ODOO_URL}/web/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        credentials: "include",
        body: formData,
        redirect: "manual", // Don't follow redirects
    });

    // 303 = success (redirect to /my)
    return response.status === 303;
}
```

**Note**: After successful signup, user is automatically logged in with session cookie.

---

### 4. Logout

**Endpoint**: `POST /web/session/destroy`

**Headers**:

```
Content-Type: application/json
Cookie: session_id=<your_session_cookie>
```

**Request Body**:

```json
{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {},
    "id": 1
}
```

**Success Response**:

```json
{
    "jsonrpc": "2.0",
    "id": 1
}
```

#### JavaScript Implementation

```typescript
async function logout(): Promise<void> {
    await fetch(`${ODOO_URL}/web/session/destroy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: {},
            id: Date.now(),
        }),
    });

    // Session cookie is now invalidated by Odoo
    // Optionally redirect to login page
}
```

**Note**: After logout, any subsequent API calls will return "Session Expired" error (code 100).

---

### 5. Get Current User Profile

After login, retrieve user profile data using the `uid` from session.

**Endpoint**: `POST /web/dataset/call_kw/res.users/read`

**Request Body**:

```json
{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {
        "model": "res.users",
        "method": "read",
        "args": [[<uid>], ["id", "name", "login", "email", "phone", "partner_id", "lang", "tz"]],
        "kwargs": {}
    },
    "id": 1
}
```

**Response**:

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "result": [
        {
            "id": 41,
            "name": "Portal User",
            "login": "user@example.com",
            "email": "user@example.com",
            "phone": "123456789",
            "partner_id": [7968, "Portal User"],
            "lang": "it_IT",
            "tz": "Europe/Rome"
        }
    ]
}
```

#### Get Full Contact Details (Partner)

For address and additional contact info, read from `res.partner` using `partner_id`:

**Endpoint**: `POST /web/dataset/call_kw/res.partner/read`

**Request Body**:

```json
{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {
        "model": "res.partner",
        "method": "read",
        "args": [[<partner_id>], ["id", "name", "email", "phone", "mobile", "street", "city", "zip", "country_id", "image_128"]],
        "kwargs": {}
    },
    "id": 1
}
```

**Response**:

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "result": [
        {
            "id": 7968,
            "name": "Portal User",
            "email": "user@example.com",
            "phone": "123456789",
            "mobile": "+39 333 1234567",
            "street": "Via Roma 1",
            "city": "Milano",
            "zip": "20100",
            "country_id": [109, "Italy"],
            "image_128": "base64_encoded_image_or_false"
        }
    ]
}
```

#### JavaScript Implementation

```typescript
interface UserProfile {
    id: number;
    name: string;
    email: string;
    phone: string | false;
    lang: string;
    tz: string | false;
    partnerId: number;
}

interface PartnerDetails {
    id: number;
    name: string;
    email: string;
    phone: string | false;
    mobile: string | false;
    street: string | false;
    city: string | false;
    zip: string | false;
    countryId: [number, string] | false;
    image128: string | false;
}

async function getCurrentUser(): Promise<UserProfile | null> {
    // First get session to obtain uid
    const session = await getSession();
    if (!session?.uid) return null;

    const response = await fetch(`${ODOO_URL}/web/dataset/call_kw/res.users/read`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: {
                model: "res.users",
                method: "read",
                args: [[session.uid], ["id", "name", "login", "email", "phone", "partner_id", "lang", "tz"]],
                kwargs: {},
            },
            id: Date.now(),
        }),
    });

    const data = await response.json();
    if (data.error || !data.result?.[0]) return null;

    const user = data.result[0];
    return {
        id: user.id,
        name: user.name,
        email: user.email || user.login,
        phone: user.phone,
        lang: user.lang,
        tz: user.tz,
        partnerId: user.partner_id[0],
    };
}

async function getPartnerDetails(partnerId: number): Promise<PartnerDetails | null> {
    const response = await fetch(`${ODOO_URL}/web/dataset/call_kw/res.partner/read`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: {
                model: "res.partner",
                method: "read",
                args: [[partnerId], ["id", "name", "email", "phone", "mobile", "street", "city", "zip", "country_id", "image_128"]],
                kwargs: {},
            },
            id: Date.now(),
        }),
    });

    const data = await response.json();
    if (data.error || !data.result?.[0]) return null;

    const partner = data.result[0];
    return {
        id: partner.id,
        name: partner.name,
        email: partner.email,
        phone: partner.phone,
        mobile: partner.mobile,
        street: partner.street,
        city: partner.city,
        zip: partner.zip,
        countryId: partner.country_id,
        image128: partner.image_128,
    };
}
```

#### Update Profile (via Portal)

Portal users cannot directly call `write` on models via RPC (security restriction).
Use the portal form endpoint instead:

**Endpoint**: `POST /my/account`

**Headers**:

```
Content-Type: application/x-www-form-urlencoded
```

**Request Body**:

```
csrf_token=<token>&name=New+Name&phone=123456789&street=Via+Roma+1&city=Milano&zipcode=20100&country_id=109
```

**Note**: Get `csrf_token` from `GET /my/account` first.

---

### 6. Query Data (Authenticated User)

After login, use `/web/dataset/call_kw` to query any model the user has access to.

#### Search Records

**Endpoint**: `POST /web/dataset/call_kw/<model>/search_read`

**Example** - Get user's orders:

```json
{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {
        "model": "sale.order",
        "method": "search_read",
        "args": [[["partner_id", "=", <partner_id>]]],
        "kwargs": {
            "fields": ["name", "date_order", "amount_total", "state"],
            "limit": 10,
            "order": "date_order desc"
        }
    },
    "id": 1
}
```

#### JavaScript Implementation

```typescript
interface SearchParams {
    model: string;
    domain?: any[];
    fields?: string[];
    limit?: number;
    offset?: number;
    order?: string;
}

async function searchRead<T>({ model, domain = [], fields = [], limit = 80, offset = 0, order = "id desc" }: SearchParams): Promise<T[]> {
    const response = await fetch(`${ODOO_URL}/web/dataset/call_kw/${model}/search_read`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: {
                model,
                method: "search_read",
                args: [domain],
                kwargs: { fields, limit, offset, order },
            },
            id: Date.now(),
        }),
    });

    const data = await response.json();
    if (data.error) {
        throw new Error(data.error.data?.message || data.error.message);
    }
    return data.result;
}

// Example usage
const orders = await searchRead<Order>({
    model: "sale.order",
    domain: [["state", "=", "sale"]],
    fields: ["name", "date_order", "amount_total"],
    limit: 10,
});
```

---

### 7. Change Password (Logged User)

Allows a logged-in user to change their own password.

**Endpoint**: `POST /web/dataset/call_kw/res.users/change_password`

**Headers**:

```
Content-Type: application/json
Cookie: session_id=<your_session_cookie>
```

**Request Body**:

```json
{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {
        "model": "res.users",
        "method": "change_password",
        "args": ["current_password", "new_password"],
        "kwargs": {}
    },
    "id": 1
}
```

**Success Response**:

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "result": true
}
```

**Error Response** (wrong current password):

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "error": {
        "code": 200,
        "message": "Odoo Server Error",
        "data": {
            "name": "odoo.exceptions.AccessDenied",
            "message": "Access Denied"
        }
    }
}
```

#### JavaScript Implementation

```typescript
async function changePassword(currentPassword: string, newPassword: string): Promise<boolean> {
    const response = await fetch(`${ODOO_URL}/web/dataset/call_kw/res.users/change_password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params: {
                model: "res.users",
                method: "change_password",
                args: [currentPassword, newPassword],
                kwargs: {},
            },
            id: Date.now(),
        }),
    });

    const data = await response.json();

    if (data.error) {
        if (data.error.data?.name === "odoo.exceptions.AccessDenied") {
            throw new Error("Current password is incorrect");
        }
        throw new Error(data.error.data?.message || "Password change failed");
    }

    return data.result === true;
}
```

**Note**: The user must provide their current password for security.

---

### 8. Reset Password (Forgot Password)

Send a password reset email to the user.

#### Step 1: Get CSRF Token

**Endpoint**: `GET /web/reset_password`

#### Step 2: Request Reset

**Endpoint**: `POST /web/reset_password`

**Headers**:

```
Content-Type: application/x-www-form-urlencoded
```

**Request Body**:

```
csrf_token=<token>&login=user@example.com
```

**Success**: HTTP 200 with confirmation message in HTML

#### JavaScript Implementation

```typescript
async function requestPasswordReset(email: string): Promise<boolean> {
    // Step 1: Get CSRF token
    const resetPage = await fetch(`${ODOO_URL}/web/reset_password`, {
        credentials: "include",
    });
    const html = await resetPage.text();
    const csrfMatch = html.match(/name="csrf_token"\s+value="([^"]+)"/);
    if (!csrfMatch) throw new Error("CSRF token not found");

    // Step 2: Submit reset request
    const formData = new URLSearchParams({
        csrf_token: csrfMatch[1],
        login: email,
    });

    const response = await fetch(`${ODOO_URL}/web/reset_password`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        credentials: "include",
        body: formData,
    });

    return response.ok;
}
```

**Note**: User will receive an email with a reset link.

---

### 9. List Available Databases

**Endpoint**: `POST /web/database/list`

**Request Body**:

```json
{
    "jsonrpc": "2.0",
    "method": "call",
    "params": {},
    "id": 1
}
```

**Response**:

```json
{
    "jsonrpc": "2.0",
    "id": 1,
    "result": ["luxurent_dev18", "other_db"]
}
```

---

## React Implementation Example

### API Client (`src/api/odoo.ts`)

```typescript
const ODOO_URL = import.meta.env.VITE_ODOO_URL || "http://dev8070:8070";
const ODOO_DB = import.meta.env.VITE_ODOO_DB || "luxurent_dev18";

interface JsonRpcResponse<T> {
    jsonrpc: string;
    id: number;
    result?: T;
    error?: {
        code: number;
        message: string;
        data?: {
            name: string;
            message: string;
        };
    };
}

interface SessionInfo {
    uid: number;
    name: string;
    username: string;
    is_admin: boolean;
    is_internal_user: boolean;
    user_context: {
        lang: string;
        tz: string;
    };
}

async function jsonRpc<T>(endpoint: string, params: object): Promise<T> {
    const response = await fetch(`${ODOO_URL}${endpoint}`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        credentials: "include", // Important for cookies
        body: JSON.stringify({
            jsonrpc: "2.0",
            method: "call",
            params,
            id: Date.now(),
        }),
    });

    const data: JsonRpcResponse<T> = await response.json();

    if (data.error) {
        throw new Error(data.error.data?.message || data.error.message);
    }

    return data.result as T;
}

// Login
export async function login(email: string, password: string): Promise<SessionInfo> {
    return jsonRpc("/web/session/authenticate", {
        db: ODOO_DB,
        login: email,
        password,
    });
}

// Get current session
export async function getSession(): Promise<SessionInfo | null> {
    try {
        return await jsonRpc("/web/session/get_session_info", {});
    } catch {
        return null;
    }
}

// Logout
export async function logout(): Promise<void> {
    await jsonRpc("/web/session/destroy", {});
}

// Check if logged in
export async function isAuthenticated(): Promise<boolean> {
    const session = await getSession();
    return session !== null && session.uid !== false;
}
```

### Auth Context (`src/context/AuthContext.tsx`)

```tsx
import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import * as odoo from "../api/odoo";

interface User {
    uid: number;
    name: string;
    email: string;
    isAdmin: boolean;
    isPortal: boolean;
}

interface AuthContextType {
    user: User | null;
    loading: boolean;
    login: (email: string, password: string) => Promise<void>;
    logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
    const [user, setUser] = useState<User | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        // Check session on mount
        odoo.getSession()
            .then((session) => {
                if (session?.uid) {
                    setUser({
                        uid: session.uid,
                        name: session.name,
                        email: session.username,
                        isAdmin: session.is_admin,
                        isPortal: !session.is_internal_user,
                    });
                }
            })
            .finally(() => setLoading(false));
    }, []);

    const login = async (email: string, password: string) => {
        const session = await odoo.login(email, password);
        setUser({
            uid: session.uid,
            name: session.name,
            email: session.username,
            isAdmin: session.is_admin,
            isPortal: !session.is_internal_user,
        });
    };

    const logout = async () => {
        await odoo.logout();
        setUser(null);
    };

    return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
    const context = useContext(AuthContext);
    if (!context) {
        throw new Error("useAuth must be used within AuthProvider");
    }
    return context;
}
```

---

## API Types: Session vs Bearer Token

| Feature            | Session API (`/web/*`)               | Bearer API (`/json/2/*`)     |
| ------------------ | ------------------------------------ | ---------------------------- |
| **Auth**           | Cookie session                       | API Token (Bearer)           |
| **Use case**       | SPA with user login                  | Server-to-server integration |
| **Login required** | Yes, via `/web/session/authenticate` | No, use pre-generated token  |
| **User context**   | Current logged user                  | Token owner (usually admin)  |
| **CORS**           | Needs proxy or config                | Needs proxy or config        |

### For SPA with Portal Users → Use Session API

```
POST /web/session/authenticate     → Login
POST /web/session/get_session_info → Check session
POST /web/dataset/call_kw/*        → Query data
POST /web/session/destroy          → Logout
```

### For Backend Integrations → Use Bearer API

```bash
# With API token (no login needed)
curl -X POST "http://server:8070/json/2/res.partner/search" \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain": [["is_company", "=", true]], "limit": 10}'
```

**Note**: Bearer tokens are created in Odoo Settings > Users > API Keys.

---

## CORS Configuration

For development, add CORS headers in Odoo. Create a custom module or use nginx proxy.

### Nginx Proxy Example

```nginx
server {
    listen 3001;

    location / {
        proxy_pass http://localhost:8070;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # CORS headers
        add_header Access-Control-Allow-Origin "http://localhost:5173" always;
        add_header Access-Control-Allow-Credentials "true" always;
        add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Content-Type, Cookie" always;

        if ($request_method = OPTIONS) {
            return 204;
        }
    }
}
```

### Vite Proxy (Development)

In `vite.config.ts`:

```typescript
export default defineConfig({
    server: {
        proxy: {
            "/web": {
                target: "http://dev8070:8070",
                changeOrigin: true,
                cookieDomainRewrite: "localhost",
            },
        },
    },
});
```

---

## Error Handling

| Error Code | Meaning         | Action             |
| ---------- | --------------- | ------------------ |
| `100`      | Session Expired | Redirect to login  |
| `200`      | Access Denied   | Show error message |
| `404`      | Not Found       | Check endpoint URL |

---

## Security Notes

1. **Always use HTTPS** in production
2. **Store session in httpOnly cookies** (handled by Odoo)
3. **Validate CSRF tokens** for form submissions
4. **Never expose admin credentials** in frontend code
5. **Use environment variables** for sensitive config

---

## Quick Test with cURL

```bash
# Login
curl -c cookies.txt -X POST "http://dev8070:8070/web/session/authenticate" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"call","params":{"db":"luxurent_dev18","login":"user@example.com","password":"pass"},"id":1}'

# Get session
curl -b cookies.txt -X POST "http://dev8070:8070/web/session/get_session_info" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"call","params":{},"id":1}'

# Logout
curl -b cookies.txt -X POST "http://dev8070:8070/web/session/destroy" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"call","params":{},"id":1}'
```
