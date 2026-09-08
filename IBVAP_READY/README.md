# IBVAP — AI Border Surveillance Command Center v4

A self-contained SIH 26187 demonstration website using one local FastAPI server, SQLite, OpenCV and bundled CCTV demo videos.

## Run on Windows
1. Extract the ZIP.
2. Open the `IBVAP_READY` folder.
3. Double-click `run.bat`.
4. Open `http://127.0.0.1:8000` if the browser does not open automatically.

## Demo login
- Username: `admin`
- Password: `IBVAP@2026`

Change the password from **System & Security → Change Password** after first login.

## New security features
- Authentication required before the dashboard or video/evidence endpoints can be used.
- Passwords stored as PBKDF2-SHA256 hashes with per-user salts.
- HttpOnly + SameSite session cookie with 8-hour expiry.
- Failed-login lockout after repeated failed attempts.
- Security response headers and restricted CORS origins.
- Video upload extension validation and 200 MB size limit.
- Uploaded temporary video is removed after analysis.
- Evidence files are deleted when a solved incident is removed.
- Safe basename handling for demo/evidence file endpoints.

## Incident storage workflow
`NEW → ACKNOWLEDGED → SOLVED → REMOVE`

An incident cannot be removed while it is NEW or ACKNOWLEDGED. The **REMOVE** action appears only after the operator marks the incident **SOLVED**. Removing a solved incident also deletes its evidence snapshot, helping control local storage usage.

## CCTV improvements
- Six live-style CCTV cards using bundled MP4 demo feeds.
- AI person box, restricted-zone overlay, LIVE indicator, FPS and signal status.
- Live preview modal for each feed.
- One-click transfer from a camera feed to Video Analysis.

## ANPR Watchlist improvements
- Search by plate, vehicle or note.
- Priority filters: High / Monitor / Low.
- Plate metadata: vehicle type, note and optional expiry.
- Simulated ANPR match button with hit count and last-seen timestamp.
- Watchlist statistics for total plates, high-priority records and matches.
- Add/remove persistent records in SQLite.

## Existing features
- Command Center statistics and risk distribution
- Four prepared CCTV scenarios
- Local OpenCV motion/person cues
- Explainable risk factors and evidence snapshots
- CSV incident export
- System health and security page

## Technical note
This is a controlled hackathon prototype. Risk scores are decision-support signals and must be verified by an authorized operator. It is not a production border-security system.


### OpenCV compatibility

This version does not require `cv2.HOGDescriptor` to exist. If the installed OpenCV build does not expose HOG, the video analysis automatically uses the motion/rule engine instead of crashing. This is intentional for compatibility with newer Python/OpenCV environments.

Run from this folder with:
`run.bat`

Then open `http://127.0.0.1:8000/login`.
