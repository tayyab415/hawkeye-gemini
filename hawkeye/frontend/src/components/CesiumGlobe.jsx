import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import * as Cesium from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";

const JAKARTA_KAMPUNG_MELAYU = {
  lat: -6.225,
  lng: 106.855,
  altitude: 8000,
};

// ── Inline SVG data URIs for 3D entity billboards ───────────────
const ENTITY_ICONS = {
  helicopter: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
    <g fill="none" stroke="#00d4ff" stroke-width="2" stroke-linecap="round">
      <line x1="10" y1="16" x2="54" y2="16"/>
      <line x1="32" y1="16" x2="32" y2="26"/>
      <ellipse cx="32" cy="34" rx="14" ry="8" fill="rgba(0,212,255,0.15)"/>
      <line x1="18" y1="34" x2="8" y2="28"/>
      <line x1="46" y1="34" x2="56" y2="40"/>
      <line x1="56" y1="38" x2="56" y2="44"/>
      <line x1="24" y1="42" x2="24" y2="50"/>
      <line x1="40" y1="42" x2="40" y2="50"/>
      <line x1="20" y1="50" x2="44" y2="50"/>
    </g>
  </svg>`)}`,
  boat: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
    <g fill="none" stroke="#00ff88" stroke-width="2" stroke-linecap="round">
      <path d="M8,40 Q16,50 32,50 Q48,50 56,40" fill="rgba(0,255,136,0.1)"/>
      <line x1="32" y1="50" x2="32" y2="20"/>
      <polygon points="32,20 48,35 32,32" fill="rgba(0,255,136,0.15)" stroke="#00ff88"/>
      <path d="M4,44 Q12,52 24,48 Q36,52 48,48 Q56,52 60,44" stroke-width="1.5"/>
    </g>
  </svg>`)}`,
  command_post: `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
    <g fill="none" stroke="#ffaa00" stroke-width="2">
      <rect x="12" y="24" width="40" height="28" rx="2" fill="rgba(255,170,0,0.1)"/>
      <polygon points="32,10 52,24 12,24" fill="rgba(255,170,0,0.08)"/>
      <line x1="32" y1="4" x2="32" y2="10"/>
      <circle cx="32" cy="4" r="2" fill="#ffaa00"/>
      <line x1="20" y1="36" x2="28" y2="36"/>
      <line x1="36" y1="36" x2="44" y2="36"/>
      <rect x="26" y="40" width="12" height="12" stroke="#ffaa00" fill="rgba(255,170,0,0.15)"/>
    </g>
  </svg>`)}`,
};

const ENTITY_COLORS = {
  helicopter: '#00d4ff',
  boat: '#00ff88',
  command_post: '#ffaa00',
};

function getStartupError(key) {
  if (!key) {
    return "Missing Google Maps API key. Set VITE_GOOGLE_MAPS_API_KEY before loading photorealistic 3D Tiles.";
  }

  if (!key.startsWith("AIza")) {
    return "The supplied key does not look like a Google Maps Platform browser key.";
  }

  return null;
}

const CesiumGlobe = forwardRef(function CesiumGlobe({ apiKey, onStatusChange }, ref) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const overlaysRef = useRef(new Map());
  const markersRef = useRef(new Map());
  // ── New refs for enhanced features ────────────────────────────
  const entitiesRef = useRef(new Map());        // entityId → Cesium.Entity
  const measureLinesRef = useRef(new Map());    // lineId → Cesium.Entity
  const threatRingsRef = useRef([]);            // Cesium.Entity[]
  const cameraIntervalRef = useRef(null);       // orbit / tracking timer
  const orbitTickHandlerRef = useRef(null);     // viewer.clock onTick handler
  const atmosphereStageRef = useRef(null);       // PostProcessStage for atmosphere
  const [status, setStatus] = useState({
    phase: "idle",
    message: "Awaiting viewer startup",
  });

  useEffect(() => {
    if (!containerRef.current || viewerRef.current) return;

    const key = apiKey || import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
    const startupError = getStartupError(key);
    if (startupError) {
      const nextStatus = { phase: "error", message: startupError };
      setStatus(nextStatus);
      onStatusChange?.(nextStatus);
      return;
    }

    const viewer = new Cesium.Viewer(containerRef.current, {
      baseLayer: false,
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      animation: false,
      timeline: false,
      fullscreenButton: false,
      vrButton: false,
      infoBox: false,
      selectionIndicator: false,
      // Preserve drawing buffer so captureScreenshot works reliably
      contextOptions: {
        webgl: { preserveDrawingBuffer: true },
      },
    });
    viewer.scene.globe.show = false;

    viewerRef.current = viewer;
    const loadingStatus = {
      phase: "loading",
      message: "Streaming Google photorealistic 3D Tiles for Jakarta...",
    };
    setStatus(loadingStatus);
    onStatusChange?.(loadingStatus);
    Cesium.RequestScheduler.requestsByServer["tile.googleapis.com:443"] = 18;

    (async () => {
      try {
        const tileset = await Cesium.Cesium3DTileset.fromUrl(
          `https://tile.googleapis.com/v1/3dtiles/root.json?key=${key}`,
          { showCreditsOnScreen: true },
        );
        if (viewer.isDestroyed()) return;
        viewer.scene.primitives.add(tileset);
        viewer.camera.flyTo({
          destination: Cesium.Cartesian3.fromDegrees(
            JAKARTA_KAMPUNG_MELAYU.lng,
            JAKARTA_KAMPUNG_MELAYU.lat,
            JAKARTA_KAMPUNG_MELAYU.altitude,
          ),
          orientation: {
            heading: Cesium.Math.toRadians(0),
            pitch: Cesium.Math.toRadians(-45),
            roll: 0,
          },
          duration: 0,
        });
        const readyStatus = {
          phase: "ready",
          message: "Google photorealistic 3D Tiles loaded. Use window.globeRef from the test page to drive the viewer.",
        };
        setStatus(readyStatus);
        onStatusChange?.(readyStatus);
      } catch (err) {
        const message = err?.message || JSON.stringify(err);
        if (!viewer.isDestroyed()) {
          viewer.scene.globe.show = true;
          viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#0a0e17");
        }
        const errorStatus = {
          phase: "error",
          message: `Failed to load Google 3D Tiles. Falling back to base Cesium globe. Confirm Tile API is enabled and the browser key is the Maps key, not the Gemini key. Details: ${message}`,
        };
        console.error("Failed to load Google 3D Tiles:", message);
        setStatus(errorStatus);
        onStatusChange?.(errorStatus);
      }
    })();

    return () => {
      overlaysRef.current.clear();
      markersRef.current.clear();
      entitiesRef.current.clear();
      measureLinesRef.current.clear();
      threatRingsRef.current = [];
      if (cameraIntervalRef.current) {
        clearInterval(cameraIntervalRef.current);
        cameraIntervalRef.current = null;
      }
      if (orbitTickHandlerRef.current && viewerRef.current && !viewerRef.current.isDestroyed()) {
        viewerRef.current.clock.onTick.removeEventListener(orbitTickHandlerRef.current);
        orbitTickHandlerRef.current = null;
      }
      if (viewerRef.current && !viewerRef.current.isDestroyed()) {
        viewerRef.current.destroy();
      }
      viewerRef.current = null;
    };
  }, [apiKey]);

  useImperativeHandle(ref, () => ({
    flyTo(
      lat = JAKARTA_KAMPUNG_MELAYU.lat,
      lng = JAKARTA_KAMPUNG_MELAYU.lng,
      altitude = 3000,
      durationSeconds = 2,
    ) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(lng, lat, altitude),
        orientation: {
          heading: Cesium.Math.toRadians(0),
          pitch: Cesium.Math.toRadians(-45),
          roll: 0,
        },
        duration: durationSeconds,
      });
    },

    async addGeoJsonOverlay(id, geojsonData, style = {}) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return id;

      // Remove existing overlay with same id
      if (overlaysRef.current.has(id)) {
        const existing = overlaysRef.current.get(id);
        viewer.dataSources.remove(existing, true);
        overlaysRef.current.delete(id);
      }

      const targetOpacity = style.opacity ?? 0.4;
      const fillColor = style.fillColor
        ? Cesium.Color.fromCssColorString(style.fillColor)
        : Cesium.Color.fromCssColorString("#00d4ff");

      const outlineCss = style.outlineColor || style.strokeColor;
      const outlineColor = outlineCss
        ? Cesium.Color.fromCssColorString(outlineCss)
        : Cesium.Color.WHITE;
      const outlineWidth = style.outlineWidth ?? style.strokeWidth ?? 2;
      const dashPattern = Array.isArray(style.dashPattern)
        ? style.dashPattern
            .map((value) => Number(value))
            .filter((value) => Number.isFinite(value) && value > 0)
            .slice(0, 2)
        : [];
      const isRoute = style.isRoute === true;
      const glowPower = Number.isFinite(style.glowPower) ? style.glowPower : 0.2;
      const routeWidth = isRoute ? Math.max(Number(outlineWidth) || 2, 8) : outlineWidth;

      const ds = await Cesium.GeoJsonDataSource.load(geojsonData, {
        clampToGround: !isRoute,
        fill: fillColor.withAlpha(0),
        stroke: outlineColor.withAlpha(0),
        strokeWidth: routeWidth,
      });

      viewer.dataSources.add(ds);
      overlaysRef.current.set(id, ds);

      if (isRoute && style.autoFocus !== false) {
        viewer.flyTo(ds, {
          duration: 1.2,
          offset: new Cesium.HeadingPitchRange(
            viewer.camera.heading,
            Cesium.Math.toRadians(-50),
            18000,
          ),
        }).catch(() => {});
      }

      let step = 0;
      const steps = 10;
      const interval = setInterval(() => {
        step++;
        const currentOpacity = (step / steps) * targetOpacity;
        // Cesium expects material/color setting on entities directly if dynamically updating after load
        ds.entities.values.forEach(entity => {
          if (entity.polygon) {
            entity.polygon.material = fillColor.withAlpha(currentOpacity);
            if (entity.polygon.outlineColor) entity.polygon.outlineColor = outlineColor.withAlpha(currentOpacity);
          }
          if (entity.polyline) {
            entity.polyline.width = routeWidth;
            entity.polyline.clampToGround = !isRoute;
            if (isRoute && dashPattern.length === 2) {
              entity.polyline.material = new Cesium.PolylineDashMaterialProperty({
                color: outlineColor.withAlpha(currentOpacity),
                gapColor: Cesium.Color.TRANSPARENT,
                dashLength: dashPattern[0],
              });
              entity.polyline.depthFailMaterial = outlineColor.withAlpha(0.98);
            } else if (isRoute) {
              entity.polyline.material = new Cesium.PolylineGlowMaterialProperty({
                color: outlineColor.withAlpha(currentOpacity),
                glowPower,
                taperPower: 0.7,
              });
              entity.polyline.depthFailMaterial = outlineColor.withAlpha(0.98);
            } else {
              entity.polyline.material = outlineColor.withAlpha(currentOpacity);
            }
          }
        });
        if (step >= steps) clearInterval(interval);
      }, 50);

      return id;
    },

    removeOverlay(id) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      const ds = overlaysRef.current.get(id);
      if (ds) {
        viewer.dataSources.remove(ds, true);
        overlaysRef.current.delete(id);
      }
    },

    addPulsingMarker(id, lat, lng, color = "#ff4444", label = "") {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return id;

      // Remove existing marker with same id
      if (markersRef.current.has(id)) {
        viewer.entities.remove(markersRef.current.get(id));
        markersRef.current.delete(id);
      }

      const markerColor = Cesium.Color.fromCssColorString(color);
      const startTime = Date.now();

      const entity = viewer.entities.add({
        position: Cesium.Cartesian3.fromDegrees(lng, lat, 50),
        point: {
          pixelSize: new Cesium.CallbackProperty(() => {
            const t = (Date.now() - startTime) / 500;
            return 12 + 6 * Math.abs(Math.sin(t));
          }, false),
          color: markerColor.withAlpha(0.9),
          outlineColor: markerColor,
          outlineWidth: new Cesium.CallbackProperty(() => {
            const t = (Date.now() - startTime) / 500;
            return 2 + 3 * Math.abs(Math.sin(t));
          }, false),
          heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: label
          ? {
              text: label,
              font: "14px sans-serif",
              fillColor: Cesium.Color.WHITE,
              outlineColor: Cesium.Color.BLACK,
              outlineWidth: 2,
              style: Cesium.LabelStyle.FILL_AND_OUTLINE,
              verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
              pixelOffset: new Cesium.Cartesian2(0, -24),
              heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
              disableDepthTestDistance: Number.POSITIVE_INFINITY,
            }
          : undefined,
      });

      markersRef.current.set(id, entity);
      return id;
    },

    removeMarker(id) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      const entity = markersRef.current.get(id);
      if (entity) {
        viewer.entities.remove(entity);
        markersRef.current.delete(id);
      }
    },

    captureScreenshot() {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) {
        console.error("[GLOBE] captureScreenshot called without an active viewer");
        return null;
      }

      viewer.render();
      const sourceCanvas = viewer.canvas;
      if (!sourceCanvas) return null;

      const MAX_SIZE = 768;
      let outputCanvas = sourceCanvas;

      if (sourceCanvas.width > MAX_SIZE || sourceCanvas.height > MAX_SIZE) {
        const scale = Math.min(MAX_SIZE / sourceCanvas.width, MAX_SIZE / sourceCanvas.height);
        const targetWidth = Math.max(1, Math.round(sourceCanvas.width * scale));
        const targetHeight = Math.max(1, Math.round(sourceCanvas.height * scale));
        const resizedCanvas = document.createElement("canvas");
        resizedCanvas.width = MAX_SIZE;
        resizedCanvas.height = MAX_SIZE;
        const ctx = resizedCanvas.getContext("2d");
        if (!ctx) return null;
        ctx.fillStyle = "#000";
        ctx.fillRect(0, 0, MAX_SIZE, MAX_SIZE);
        const dx = Math.round((MAX_SIZE - targetWidth) / 2);
        const dy = Math.round((MAX_SIZE - targetHeight) / 2);
        ctx.drawImage(sourceCanvas, dx, dy, targetWidth, targetHeight);
        outputCanvas = resizedCanvas;
      }

      const dataUrl = outputCanvas.toDataURL("image/jpeg", 0.68);
      return dataUrl.replace(/^data:image\/jpeg;base64,/, "");
    },

    getViewer() {
      return viewerRef.current;
    },

    // ────────────────────────────────────────────────────────────
    // F4: Camera Modes
    // ────────────────────────────────────────────────────────────

    /**
     * Orbit camera — slowly rotates heading around the current center.
     */
    startOrbitCamera(lat = JAKARTA_KAMPUNG_MELAYU.lat, lng = JAKARTA_KAMPUNG_MELAYU.lng) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      this.stopCameraMode();

      const center = Cesium.Cartesian3.fromDegrees(lng, lat, 0);
      const range = 5500;
      const pitch = Cesium.Math.toRadians(-35);
      let heading = viewer.camera.heading;

      const orbitTick = () => {
        if (!viewer || viewer.isDestroyed()) return;
        heading += Cesium.Math.toRadians(0.2);
        viewer.camera.lookAt(center, new Cesium.HeadingPitchRange(heading, pitch, range));
      };

      orbitTickHandlerRef.current = orbitTick;
      viewer.clock.onTick.addEventListener(orbitTick);
    },

    /**
     * Bird-eye camera — looks straight down from high altitude.
     */
    setBirdEyeCamera(lat = JAKARTA_KAMPUNG_MELAYU.lat, lng = JAKARTA_KAMPUNG_MELAYU.lng) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      this.stopCameraMode();
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(lng, lat, 10000),
        orientation: {
          heading: 0,
          pitch: -Math.PI / 2,
          roll: 0,
        },
        duration: 1.5,
      });
    },

    /**
     * Street-level camera — low altitude, forward-looking.
     */
    setStreetLevelCamera(lat = JAKARTA_KAMPUNG_MELAYU.lat, lng = JAKARTA_KAMPUNG_MELAYU.lng) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      this.stopCameraMode();
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(lng, lat, 50),
        orientation: {
          heading: 0,
          pitch: -0.1,
          roll: 0,
        },
        duration: 1.5,
      });
    },

    /**
     * Overview camera — wide-area view at moderate altitude.
     */
    setOverviewCamera() {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      this.stopCameraMode();
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(
          JAKARTA_KAMPUNG_MELAYU.lng,
          JAKARTA_KAMPUNG_MELAYU.lat,
          25000,
        ),
        orientation: {
          heading: Cesium.Math.toRadians(0),
          pitch: Cesium.Math.toRadians(-45),
          roll: 0,
        },
        duration: 2,
      });
    },

    /**
     * Track an entity — fly camera to the entity's position.
     */
    trackEntity(entityId) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      const entity = entitiesRef.current.get(entityId);
      if (!entity) return;
      this.stopCameraMode();
      viewer.trackedEntity = entity;
    },

    /**
     * Stop any active camera mode (orbit timer, tracking, etc.)
     */
    stopCameraMode() {
      if (cameraIntervalRef.current) {
        clearInterval(cameraIntervalRef.current);
        cameraIntervalRef.current = null;
      }
      const viewer = viewerRef.current;
      if (viewer && !viewer.isDestroyed()) {
        if (orbitTickHandlerRef.current) {
          viewer.clock.onTick.removeEventListener(orbitTickHandlerRef.current);
          orbitTickHandlerRef.current = null;
        }
        viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
        viewer.trackedEntity = undefined;
      }
    },

    // ────────────────────────────────────────────────────────────
    // F2: 3D Entity Management (helicopter, boat, command post)
    // ────────────────────────────────────────────────────────────

    /**
     * Deploy a 3D entity with inline SVG billboard.
     * @param {string} id - Unique entity identifier
     * @param {string} entityType - 'helicopter' | 'boat' | 'command_post'
     * @param {number} lat
     * @param {number} lng
     * @param {object|number} [optionsOrAltitude={}]
     * @param {string} [legacyLabel]
     */
    addEntity(id, entityType, lat, lng, optionsOrAltitude = {}, legacyLabel = '') {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return id;

      const options = typeof optionsOrAltitude === "object" && optionsOrAltitude !== null
        ? optionsOrAltitude
        : { altitude: optionsOrAltitude, label: legacyLabel };
      const altitude = options.altitude ?? 100;
      const label = options.label ?? "";

      // Remove existing entity with same id
      if (entitiesRef.current.has(id)) {
        viewer.entities.remove(entitiesRef.current.get(id));
        entitiesRef.current.delete(id);
      }

      const iconUri = ENTITY_ICONS[entityType] || ENTITY_ICONS.command_post;
      const color = ENTITY_COLORS[entityType] || '#ffaa00';
      const entityColor = Cesium.Color.fromCssColorString(color);

      const entity = viewer.entities.add({
        id: `entity-${id}`,
        position: Cesium.Cartesian3.fromDegrees(lng, lat, altitude),
        billboard: {
          image: iconUri,
          width: 48,
          height: 48,
          verticalOrigin: Cesium.VerticalOrigin.CENTER,
          horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
          heightReference: altitude > 50
            ? Cesium.HeightReference.NONE
            : Cesium.HeightReference.RELATIVE_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: label ? {
          text: label,
          font: '11px monospace',
          fillColor: entityColor,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.TOP,
          pixelOffset: new Cesium.Cartesian2(0, 28),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          scaleByDistance: new Cesium.NearFarScalar(500, 1, 15000, 0.3),
        } : undefined,
      });

      entitiesRef.current.set(id, entity);
      return id;
    },

    /**
     * Move an existing entity to a new position with smooth interpolation.
     */
    moveEntity(id, lat, lng, durationOrOptions = 2000, legacyDurationMs = null) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;

      const entity = entitiesRef.current.get(id);
      if (!entity) return;

      const startPos = entity.position?.getValue?.(viewer.clock.currentTime);
      let altitude = 100;
      let durationMs = 2000;

      if (startPos) {
        const currentCartographic = Cesium.Cartographic.fromCartesian(startPos);
        altitude = currentCartographic?.height ?? altitude;
      }

      if (typeof durationOrOptions === "object" && durationOrOptions !== null) {
        altitude = durationOrOptions.altitude ?? altitude;
        durationMs = durationOrOptions.durationMs ?? durationOrOptions.duration ?? durationMs;
      } else if (legacyDurationMs !== null && legacyDurationMs !== undefined) {
        altitude = durationOrOptions ?? altitude;
        durationMs = legacyDurationMs;
      } else {
        durationMs = durationOrOptions ?? durationMs;
      }

      const endPos = Cesium.Cartesian3.fromDegrees(lng, lat, altitude);
      if (!startPos) {
        entity.position = endPos;
        return;
      }

      const startTime = Date.now();
      entity.position = new Cesium.CallbackProperty(() => {
        const elapsed = Date.now() - startTime;
        const t = Math.min(1, elapsed / durationMs);
        // Smooth ease-in-out
        const ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        return Cesium.Cartesian3.lerp(startPos, endPos, ease, new Cesium.Cartesian3());
      }, false);

      // Settle to static position after animation completes
      setTimeout(() => {
        if (entity && !viewer.isDestroyed()) {
          entity.position = endPos;
        }
      }, durationMs + 100);
    },

    /**
     * Remove a deployed entity.
     */
    removeEntity(id) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      const entity = entitiesRef.current.get(id);
      if (entity) {
        viewer.entities.remove(entity);
        entitiesRef.current.delete(id);
      }
    },

    // ────────────────────────────────────────────────────────────
    // F5: Flood Animation (extruded polygon + animated level)
    // ────────────────────────────────────────────────────────────

    /**
     * Add a flood overlay with extruded height representing water depth.
     */
    async addFloodOverlay(id, geojsonData, initialLevelM = 4) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return id;

      // Remove existing
      if (overlaysRef.current.has(id)) {
        viewer.dataSources.remove(overlaysRef.current.get(id), true);
        overlaysRef.current.delete(id);
      }

      const ds = await Cesium.GeoJsonDataSource.load(geojsonData, {
        clampToGround: false,
      });
      viewer.dataSources.add(ds);
      overlaysRef.current.set(id, ds);

      // ── Severity gradient: compute area range across all polygons ──
      const entities = ds.entities.values;
      let minArea = Infinity;
      let maxArea = 0;
      for (const e of entities) {
        const area = e.properties?.polygon_area_sqkm?.getValue?.()
          ?? e.properties?.area_sqm?.getValue?.()
          ?? 0;
        // Normalise to sqkm
        const areaSqKm = area > 1000 ? area / 1_000_000 : area;
        if (areaSqKm > 0) {
          minArea = Math.min(minArea, areaSqKm);
          maxArea = Math.max(maxArea, areaSqKm);
        }
      }
      if (!Number.isFinite(minArea) || minArea === Infinity) minArea = 0;
      const areaRange = maxArea - minArea || 1;

      // Severity colour ramp: light cyan (#00b4ff) → deep blue (#001a66)
      const colorLow = { r: 0 / 255, g: 180 / 255, b: 255 / 255 };
      const colorHigh = { r: 0 / 255, g: 26 / 255, b: 102 / 255 };

      const startTime = Date.now();
      for (const entity of entities) {
        if (!entity.polygon) continue;

        // Per-polygon severity (0 = smallest, 1 = largest)
        const rawArea = entity.properties?.polygon_area_sqkm?.getValue?.()
          ?? entity.properties?.area_sqm?.getValue?.()
          ?? 0;
        const areaSqKm = rawArea > 1000 ? rawArea / 1_000_000 : rawArea;
        const severity = Math.min(1, Math.max(0, (areaSqKm - minArea) / areaRange));

        // Lerp colour between low and high
        const r = colorLow.r + (colorHigh.r - colorLow.r) * severity;
        const g = colorLow.g + (colorHigh.g - colorLow.g) * severity;
        const b = colorLow.b + (colorHigh.b - colorLow.b) * severity;
        const baseColor = new Cesium.Color(r, g, b);

        // Extrusion: larger polygons get taller (100m–500m range for globe visibility)
        const extrudedH = 100 + severity * 400;

        entity.polygon.height = 0;
        entity.polygon.extrudedHeight = extrudedH;
        entity.polygon.heightReference = Cesium.HeightReference.CLAMP_TO_GROUND;
        entity.polygon.material = new Cesium.ColorMaterialProperty(
          new Cesium.CallbackProperty(() => {
            const t = (Date.now() - startTime) / 4000;
            const alpha = 0.55 + 0.10 * Math.sin(t * Math.PI * 2);
            return baseColor.withAlpha(alpha);
          }, false)
        );
        entity.polygon.outline = true;
        entity.polygon.outlineColor = Cesium.Color.fromCssColorString('#00d4ff').withAlpha(0.7);
      }

      return id;
    },

    /**
     * Animate flood level change — smoothly interpolate extruded height.
     */
    updateFloodLevel(id, newLevelM, durationMs = 3000) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;

      const ds = overlaysRef.current.get(id);
      if (!ds) return;

      ds.entities.values.forEach((entity) => {
        if (!entity.polygon) return;
        const currentLevel = entity.polygon.extrudedHeight?.getValue?.() ?? 4;
        const startTime = Date.now();

        entity.polygon.extrudedHeight = new Cesium.CallbackProperty(() => {
          const elapsed = Date.now() - startTime;
          const t = Math.min(1, elapsed / durationMs);
          const ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
          return currentLevel + (newLevelM - currentLevel) * ease;
        }, false);

        // Settle to static value
        setTimeout(() => {
          if (entity.polygon && !viewer.isDestroyed()) {
            entity.polygon.extrudedHeight = newLevelM;
          }
        }, durationMs + 100);
      });
    },

    // ────────────────────────────────────────────────────────────
    // F9: Measurement Lines
    // ────────────────────────────────────────────────────────────

    /**
     * Add a measurement line between two points with distance label.
     */
    addMeasurementLine(id, fromOrLat1, toOrLng1, lat2OrLabel, lng2Maybe, labelMaybe = "") {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return id;

      let lat1 = fromOrLat1;
      let lng1 = toOrLng1;
      let lat2 = lat2OrLabel;
      let lng2 = lng2Maybe;
      let customLabel = labelMaybe;

      if (
        typeof fromOrLat1 === "object" &&
        fromOrLat1 !== null &&
        typeof toOrLng1 === "object" &&
        toOrLng1 !== null
      ) {
        lat1 = fromOrLat1.lat;
        lng1 = fromOrLat1.lng;
        lat2 = toOrLng1.lat;
        lng2 = toOrLng1.lng;
        customLabel = typeof lat2OrLabel === "string" ? lat2OrLabel : "";
      }

      if (
        lat1 === undefined ||
        lng1 === undefined ||
        lat2 === undefined ||
        lng2 === undefined
      ) {
        return id;
      }

      // Remove existing
      this.removeMeasurementLine(id);

      const pos1 = Cesium.Cartesian3.fromDegrees(lng1, lat1, 30);
      const pos2 = Cesium.Cartesian3.fromDegrees(lng2, lat2, 30);

      // Calculate distance
      const carto1 = Cesium.Cartographic.fromDegrees(lng1, lat1);
      const carto2 = Cesium.Cartographic.fromDegrees(lng2, lat2);
      const geodesic = new Cesium.EllipsoidGeodesic(carto1, carto2);
      const distM = geodesic.surfaceDistance;
      const distLabel = distM >= 1000
        ? `${(distM / 1000).toFixed(2)} km`
        : `${Math.round(distM)} m`;
      const measurementLabel = customLabel || distLabel;

      // Midpoint for label
      const midLng = (lng1 + lng2) / 2;
      const midLat = (lat1 + lat2) / 2;

      const startTime = Date.now();
      const entity = viewer.entities.add({
        id: `measure-${id}`,
        polyline: {
          positions: [pos1, pos2],
          width: 2,
          material: new Cesium.PolylineDashMaterialProperty({
            color: new Cesium.CallbackProperty(() => {
              const t = (Date.now() - startTime) / 1000;
              const pulse = 0.6 + 0.3 * Math.sin(t * Math.PI * 2);
              return Cesium.Color.fromCssColorString('#00d4ff').withAlpha(pulse);
            }, false),
            dashLength: 12,
          }),
          clampToGround: true,
        },
        position: Cesium.Cartesian3.fromDegrees(midLng, midLat, 40),
        label: {
          text: measurementLabel,
          font: '12px monospace',
          fillColor: Cesium.Color.fromCssColorString('#00d4ff'),
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -8),
          heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
          scaleByDistance: new Cesium.NearFarScalar(500, 1, 15000, 0.3),
        },
      });

      measureLinesRef.current.set(id, entity);
      return id;
    },

    /**
     * Remove a measurement line.
     */
    removeMeasurementLine(id) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;
      const entity = measureLinesRef.current.get(id);
      if (entity) {
        viewer.entities.remove(entity);
        measureLinesRef.current.delete(id);
      }
    },

    addThreatRings(lat, lng, rings = [1, 2, 3]) {
      console.log("[GLOBE] addThreatRings called:", { lat, lng, rings });
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;

      threatRingsRef.current.forEach((entity) => {
        try {
          viewer.entities.remove(entity);
        } catch {
          // noop
        }
      });
      threatRingsRef.current = [];

      const palette = ["#ffaa00", "#ff7700", "#ff4444", "#ff1166"];
      rings.forEach((ring, index) => {
        const radiusMeters = Math.max(250, Number(ring || 1) * 1000);
        const color = Cesium.Color.fromCssColorString(palette[index % palette.length]);
        const entity = viewer.entities.add({
          position: Cesium.Cartesian3.fromDegrees(lng, lat, 20),
          ellipse: {
            semiMajorAxis: radiusMeters,
            semiMinorAxis: radiusMeters,
            material: color.withAlpha(0.08),
            outline: true,
            outlineColor: color.withAlpha(0.65),
            outlineWidth: 2,
            heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
            classificationType: Cesium.ClassificationType.BOTH,
          },
          label: {
            text: `${ring} km`,
            font: "11px monospace",
            fillColor: color.withAlpha(0.85),
            outlineColor: Cesium.Color.BLACK,
            outlineWidth: 2,
            style: Cesium.LabelStyle.FILL_AND_OUTLINE,
            pixelOffset: new Cesium.Cartesian2(radiusMeters / 30, 0),
            heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
        threatRingsRef.current.push(entity);
      });
    },

    removeAllEntities() {
      console.log("[GLOBE] removeAllEntities called:", {});
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;

      this.stopCameraMode();

      markersRef.current.forEach((entity) => {
        viewer.entities.remove(entity);
      });
      markersRef.current.clear();

      entitiesRef.current.forEach((entity) => {
        viewer.entities.remove(entity);
      });
      entitiesRef.current.clear();

      measureLinesRef.current.forEach((entity) => {
        viewer.entities.remove(entity);
      });
      measureLinesRef.current.clear();

      threatRingsRef.current.forEach((entity) => {
        viewer.entities.remove(entity);
      });
      threatRingsRef.current = [];

      overlaysRef.current.forEach((ds) => {
        viewer.dataSources.remove(ds, true);
      });
      overlaysRef.current.clear();
    },

    // ────────────────────────────────────────────────────────────
    // F10: Tactical Atmosphere (PostProcessStage)
    // ────────────────────────────────────────────────────────────

    /**
     * Apply a fullscreen post-process tint to simulate atmospheric conditions.
     * @param {'clear'|'haze'|'night'|'thermal'|'storm'} mode
     */
    setAtmosphere(mode) {
      const viewer = viewerRef.current;
      if (!viewer || viewer.isDestroyed()) return;

      // Remove existing atmosphere stage
      if (atmosphereStageRef.current) {
        try {
          viewer.scene.postProcessStages.remove(atmosphereStageRef.current);
        } catch { /* ok */ }
        atmosphereStageRef.current = null;
      }

      if (mode === 'clear') return;

      // GLSL fragment shaders for each atmosphere mode
      const shaders = {
        haze: `
          uniform sampler2D colorTexture;
          in vec2 v_textureCoordinates;
          void main() {
            vec4 color = texture(colorTexture, v_textureCoordinates);
            float gray = dot(color.rgb, vec3(0.299, 0.587, 0.114));
            vec3 hazed = mix(color.rgb, vec3(0.6, 0.65, 0.75), 0.25);
            out_FragColor = vec4(hazed, color.a);
          }
        `,
        night: `
          uniform sampler2D colorTexture;
          in vec2 v_textureCoordinates;
          void main() {
            vec4 color = texture(colorTexture, v_textureCoordinates);
            float lum = dot(color.rgb, vec3(0.299, 0.587, 0.114));
            vec3 nightVision = vec3(lum * 0.2, lum * 0.8, lum * 0.3);
            out_FragColor = vec4(nightVision, color.a);
          }
        `,
        thermal: `
          uniform sampler2D colorTexture;
          in vec2 v_textureCoordinates;
          void main() {
            vec4 color = texture(colorTexture, v_textureCoordinates);
            float lum = dot(color.rgb, vec3(0.299, 0.587, 0.114));
            vec3 thermal;
            if (lum < 0.33) {
              thermal = mix(vec3(0.0, 0.0, 0.5), vec3(1.0, 0.0, 0.0), lum / 0.33);
            } else if (lum < 0.66) {
              thermal = mix(vec3(1.0, 0.0, 0.0), vec3(1.0, 1.0, 0.0), (lum - 0.33) / 0.33);
            } else {
              thermal = mix(vec3(1.0, 1.0, 0.0), vec3(1.0, 1.0, 1.0), (lum - 0.66) / 0.34);
            }
            out_FragColor = vec4(thermal, color.a);
          }
        `,
        storm: `
          uniform sampler2D colorTexture;
          in vec2 v_textureCoordinates;
          void main() {
            vec4 color = texture(colorTexture, v_textureCoordinates);
            vec2 uv = v_textureCoordinates;
            float t = czm_frameNumber / 60.0;

            /* ── restrained storm grading: keep map readable ── */
            float grey = dot(color.rgb, vec3(0.299, 0.587, 0.114));
            vec3 coolGrey = vec3(grey * 0.82, grey * 0.86, grey * 0.94);
            color.rgb = mix(color.rgb, coolGrey, 0.12);
            color.rgb *= 0.92;

            /* ── horizon/edge mood instead of full-screen rain overlay ── */
            float edge = smoothstep(0.28, 0.92, length(uv - 0.5) * 1.15);
            float topHaze = smoothstep(0.18, 0.78, uv.y);
            float atmosphere = max(edge * 0.45, topHaze * 0.35);
            color.rgb = mix(color.rgb, color.rgb * vec3(0.84, 0.88, 0.95), atmosphere * 0.18);

            /* ── subtle cloud shadow drift near top of frame ── */
            float cloud = sin(uv.x * 9.0 + t * 0.22) * 0.5 + 0.5;
            float cloudBand = smoothstep(0.52, 0.98, uv.y) * cloud;
            color.rgb *= 1.0 - cloudBand * 0.06;

            /* ── rare soft lightning accent ── */
            float flashSeed = floor(t * 0.22);
            float flash = step(0.975, fract(sin(flashSeed * 91.17) * 43758.5453));
            float flashFade = pow(max(0.0, 1.0 - fract(t * 0.22) * 3.0), 2.0);
            color.rgb += flash * flashFade * 0.10;

            /* ── very soft vignette only at far edges ── */
            float vignette = 1.0 - smoothstep(0.62, 1.08, length(uv - 0.5) * 1.08);
            color.rgb *= mix(0.90, 1.0, vignette);

            out_FragColor = vec4(color.rgb, color.a);
          }
        `,
      };

      const fragmentShader = shaders[mode];
      if (!fragmentShader) return;

      try {
        const stage = new Cesium.PostProcessStage({ fragmentShader });
        viewer.scene.postProcessStages.add(stage);
        atmosphereStageRef.current = stage;
      } catch (err) {
        console.warn('[CesiumGlobe] PostProcessStage not supported:', err);
      }
    },
  }));

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div
        ref={containerRef}
        style={{ width: "100%", height: "100%", position: "relative" }}
      />
      {status.phase !== "ready" && (
        <div style={overlayStyles.backdrop}>
          <div style={overlayStyles.card}>
            <div style={overlayStyles.phase}>{status.phase.toUpperCase()}</div>
            <div style={overlayStyles.message}>{status.message}</div>
          </div>
        </div>
      )}
    </div>
  );
});

const overlayStyles = {
  backdrop: {
    position: "absolute",
    inset: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "linear-gradient(rgba(10, 14, 23, 0.28), rgba(10, 14, 23, 0.52))",
    pointerEvents: "none",
  },
  card: {
    maxWidth: 420,
    padding: "14px 16px",
    borderRadius: 8,
    border: "1px solid rgba(0, 212, 255, 0.18)",
    background: "rgba(10, 14, 23, 0.92)",
    color: "#e0e6ed",
    boxShadow: "0 18px 50px rgba(0, 0, 0, 0.3)",
  },
  phase: {
    marginBottom: 6,
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: 1.2,
    color: "#00d4ff",
  },
  message: {
    fontSize: 13,
    lineHeight: 1.5,
  },
};

export default CesiumGlobe;
