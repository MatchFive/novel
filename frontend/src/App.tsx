import { Routes, Route, Navigate } from "react-router-dom";
import AppShell from "./components/AppShell";
import HomePage from "./pages/HomePage";
import SettingsPage from "./pages/SettingsPage";
import LongWorkspace from "./pages/LongWorkspace";
import ShortStudio from "./pages/ShortStudio";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/project/long/:id" element={<LongWorkspace />} />
        <Route path="/project/short/:id" element={<ShortStudio />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
