## OAuth Flow Issues

### 1. OAuth state consumption is race-prone

Location:
- `sms-gateway/src/oauth/manager.py`
- `validate_state()` around the read/update sequence

Problem:
- The code reads the OAuth state document, checks whether it is valid and unused, and then marks it as used in a separate write.
- Two concurrent callbacks can both read the same state before either update lands.
- That allows the same OAuth state to be consumed twice.

Impact:
- Replay weakness in the callback flow.
- Duplicate side effects such as double token storage or duplicate Gmail watch registration.

Suggested fix:
- Use a Firestore transaction or conditional write so validation and consumption happen atomically.

### 2. Token storage is not atomic across Firestore and Secret Manager

Location:
- `sms-gateway/src/oauth/manager.py`
- `store_tokens()`
- Related agent-side refresh path in `agents/schoopet/oauth_client.py`

Problem:
- The access token record is written to Firestore first.
- The refresh token is stored in Secret Manager afterward.
- If the Secret Manager write fails, the function returns failure but still leaves a live access token document behind.

Impact:
- OAuth can appear to work initially because the access token is valid for a while.
- Refresh later fails because there is no corresponding refresh token stored.
- Produces delayed, hard-to-diagnose failures.

Suggested fix:
- Make the write sequence recoverable or compensating.
- At minimum, delete or roll back the Firestore token doc if refresh token persistence fails.
- Apply the same discipline to the agent-side token refresh write path.

### 3. Calendar tool scope does not match granted OAuth scope

Location:
- `agents/schoopet/calendar_tool.py`
- `sms-gateway/src/config.py`

Problem:
- The calendar tool requests `https://www.googleapis.com/auth/calendar`.
- The OAuth flow grants `https://www.googleapis.com/auth/calendar.events`.
- Those are not the same scope.

Impact:
- Current event operations may still work, but this is brittle.
- Scope-sensitive code paths or future validation can fail with confusing 403 errors.

Suggested fix:
- Align the tool-declared scope with the actual granted scope, or expand the granted scope intentionally if broader access is required.

### 4. Agent-side refresh overwrites email with `"unknown"`

Location:
- `agents/schoopet/oauth_client.py`
- `_refresh_access_token()`

Problem:
- On refresh, the Firestore token document is updated with `email: "unknown"`.

Impact:
- Connected-account reporting becomes unreliable.
- Makes debugging and status UX worse because the linked account identity is lost.

Suggested fix:
- Preserve the existing email from the stored token document when refreshing.

### 5. OAuth callback HTML interpolates unescaped values

Location:
- `sms-gateway/src/oauth/handler.py`
- `_success_html()` and `_error_html()`

Problem:
- `email`, `error`, and `error_description` are inserted directly into HTML responses.

Impact:
- This creates an avoidable HTML injection/XSS surface on the OAuth callback pages.

Suggested fix:
- Escape all interpolated values before rendering HTML.
