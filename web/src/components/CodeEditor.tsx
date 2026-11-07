import { python } from "@codemirror/lang-python";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

/**
 * CodeMirror 6 editor (MIT), mounted in a shadow root. CodeMirror's style library (style-mod) uses constructable
 * stylesheets inside shadow roots but a <style> element in the main document, which the strict CSP (DEC-0018,
 * `style-src 'self'`) blocks — so the shadow root keeps the CSP strict without nonces or 'unsafe-inline'.
 * Python highlighting for .py files, plain text otherwise.
 */
export function CodeEditor({ docKey, value, path, onChange, onSave }: {
  docKey: string;
  value: string;
  path: string;
  onChange: (text: string) => void;
  onSave: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const handlers = useRef({ onChange, onSave });
  handlers.current = { onChange, onSave };

  useEffect(() => {
    if (!host.current) return;
    const shadow = host.current.shadowRoot ?? host.current.attachShadow({ mode: "open" });
    const layout = new CSSStyleSheet();
    layout.replaceSync(":host { display: block; } .wrap { height: 100%; }");
    shadow.adoptedStyleSheets = [layout];
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    shadow.replaceChildren(wrap);
    const view = new EditorView({
      root: shadow,
      parent: wrap,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          ...(path.endsWith(".py") ? [python()] : []),
          keymap.of([{ key: "Mod-s", preventDefault: true, run: () => (handlers.current.onSave(), true) }]),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) handlers.current.onChange(u.state.doc.toString());
          }),
          EditorView.theme({ "&": { height: "100%" }, ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" } }),
        ],
      }),
    });
    return () => view.destroy();
    // A new document (file switch / reload) recreates the editor; typing does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docKey]);

  return <div ref={host} className="code-editor" aria-label={`editor for ${path}`} />;
}
