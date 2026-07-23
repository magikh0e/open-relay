import { useAuth } from "./auth.jsx";
import Login from "./pages/Login.jsx";
import ChatShell from "./components/ChatShell.jsx";

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <div className="center muted">Loading…</div>;
  return user ? <ChatShell /> : <Login />;
}
