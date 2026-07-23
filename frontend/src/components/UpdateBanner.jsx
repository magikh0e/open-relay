import { APP_NAME } from "../version.js";

// Shown when a newer build is deployed; refreshing loads the new version.
export default function UpdateBanner() {
  return (
    <div className="update-banner">
      <span>✨ A new version of {APP_NAME} is available.</span>
      <button onClick={() => window.location.reload()}>Refresh</button>
    </div>
  );
}
