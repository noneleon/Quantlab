import { useParams } from "react-router-dom";

import { moduleById } from "../modules";

/** Honest placeholder for modules that are not built yet (UI-008): no controls, just what and when. */
export function ModulePage() {
  const module = moduleById(useParams().id ?? "");
  if (!module) return <p className="muted">Unknown module.</p>;
  return (
    <section>
      <h1>{module.title}</h1>
      <div className="notice">
        <p>
          <strong>{module.status === "external" ? "Available outside the web UI." : "Not available yet."}</strong>{" "}
          {module.summary}
        </p>
        <p className="muted">
          Roadmap phase {module.phase} · features {module.features.join(", ")} (see docs/ROADMAP.md and
          docs/IMPLEMENTATION_STATUS.md).
        </p>
      </div>
    </section>
  );
}
