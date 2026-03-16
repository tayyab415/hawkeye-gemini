import { useEffect, useRef, useState } from "react";
import CesiumGlobe from "./components/CesiumGlobe.jsx";
import GlobeTestPanel from "./components/GlobeTestPanel.jsx";

export default function GlobeTest() {
  const globeRef = useRef(null);
  const [status, setStatus] = useState({
    phase: "idle",
    message: "Viewer not started",
  });

  useEffect(() => {
    window.globeRef = globeRef.current;
    return () => {
      window.globeRef = null;
    };
  });

  return (
    <div style={{ width: "100vw", height: "100vh", position: "relative", background: "#0a0e17" }}>
      <CesiumGlobe
        ref={globeRef}
        apiKey={import.meta.env.VITE_GOOGLE_MAPS_API_KEY}
        onStatusChange={setStatus}
      />
      <GlobeTestPanel globeRef={globeRef} status={status} />
    </div>
  );
}
