import { useAuth } from "./auth.jsx";
import Login from "./pages/Login.jsx";
import ChatShell from "./components/ChatShell.jsx";
import UpdateBanner from "./components/UpdateBanner.jsx";
import { useVersionCheck } from "./useVersionCheck.js";

export default function App() {
  const { user, loading } = useAuth();
  const updateAvailable = useVersionCheck();

  return (
    <>
      {updateAvailable && <UpdateBanner />}
      {loading ? (
        <div className="center muted">Loading…</div>
      ) : user ? (
        <ChatShell />
      ) : (
        <Login />
      )}
    </>
  );
}
