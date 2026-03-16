import React from "react";
import ReactDOM from "react-dom/client";
import GlobeTest from "./GlobeTest.jsx";

const style = document.createElement("style");
style.textContent = `
  *, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
  html, body, #root { width: 100%; height: 100%; overflow: hidden; background: #0a0e17; }
  .cesium-viewer-bottom { font-size: 10px !important; opacity: 0.6; }
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #2a3a4e; border-radius: 2px; }
`;
document.head.appendChild(style);

// No StrictMode — CesiumJS WebGL viewer doesn't survive double-mount/unmount
ReactDOM.createRoot(document.getElementById("root")).render(<GlobeTest />);
