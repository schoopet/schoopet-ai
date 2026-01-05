# Schoopet - Project Context

## Project Overview

**Schoopet** is a landing page website for a whimsical AI memory assistant service aimed at helping users with ADHD, reminders, and habit tracking via SMS. The branding focuses on a friendly, magical "sidekick" vibe rather than a sterile assistant.

The project is built as a static site using **Vite** for tooling and bundling. It utilizes vanilla HTML, CSS, and JavaScript without a heavy frontend framework like React or Vue.

### Key Architecture
*   **Entry Points:**
    *   `index.html`: The main landing page.
    *   `signup.html`: The sign-up page, now featuring a formal 4-step onboarding process.
    *   `privacy.html`: The privacy policy (updated from the original `p.html` verbatim).
    *   `terms.html`: Terms of service.
*   **Logic:** `src/main.js` handles client-side interactivity (e.g., scroll effects).
*   **Styling:** `style.css` contains the global styles, implementing a "glassmorphism" aesthetic.
*   **Bundler:** Vite handles the dev server, hot module replacement (HMR), and production builds.

## Building and Running

This project uses `npm` for dependency management and script execution.

### Prerequisites
*   Node.js installed.
*   Google Cloud SDK installed (for deployment).

### Commands

*   **Install Dependencies:**
    ```bash
    npm install
    ```

*   **Start Development Server:**
    Runs the app in development mode with HMR.
    ```bash
    npm run dev
    ```
    *   Access local server at the URL provided in the terminal (usually `http://localhost:5173`).

*   **Build for Production:**
    Compiles and minifies code into the `dist/` directory.
    ```bash
    npm run build
    ```

*   **Preview Production Build:**
    Locally preview the production build to ensure it works as expected.
    ```bash
    npm run preview
    ```

## Deployment

The website is hosted as a static site on **Google Cloud Storage (GCS)** and served via a custom domain.

### Infrastructure
*   **GCS Bucket:** `gs://www.schoopet.com`
*   **Project:** `mmontan-ml`
*   **Domain:** `schoopet.com` (managed via Namecheap).
*   **DNS Config:**
    *   `www` (CNAME) -> `c.storage.googleapis.com.`
    *   `@` (URL Redirect) -> `http://www.schoopet.com`

### Deployment Script
A helper script `deploy.sh` is provided to automate the build and upload process:
```bash
./deploy.sh
```
This script runs `npm run build`, uploads the `dist/` contents to GCS, and ensures the bucket has the correct public permissions and website configuration.

## Development Conventions

*   **HTML Structure:** Semantic HTML5.
*   **CSS:** Vanilla CSS linked directly in HTML. Looks for classes like `.glass-card`, `.gradient-text`, and `.animate-up` for specific visual effects.
*   **JavaScript:** ES Modules (`type="module"` in `package.json`). Import local scripts relative to the file.
*   **Vite Configuration:** Multi-page configuration is handled in `vite.config.js` via `rollupOptions.input`. New pages must be registered there to be included in the build.
