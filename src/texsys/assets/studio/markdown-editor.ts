import {
  Editor,
  defaultValueCtx,
  editorViewCtx,
  editorViewOptionsCtx,
  parserCtx,
  rootCtx,
  serializerCtx,
} from "@milkdown/kit/core";
import { commonmark } from "@milkdown/kit/preset/commonmark";
import { gfm } from "@milkdown/kit/preset/gfm";
import { history } from "@milkdown/kit/plugin/history";
import { clipboard } from "@milkdown/kit/plugin/clipboard";
import { EditorState, Plugin, TextSelection } from "@milkdown/kit/prose/state";
import { $prose } from "@milkdown/kit/utils";

/** A visual editor for the shared Markdown buffer, never a separate saved document. */
export async function createMarkdownEditor({
  root,
  value,
  label,
  onChange,
  onFocus,
}: {
  root: HTMLElement;
  value: string;
  label: string;
  onChange: (value: string) => void;
  onFocus?: () => void;
}) {
  let applying = false;
  let currentValue = value;
  let destroyed = false;
  const changes = $prose(
    (ctx) =>
      new Plugin({
        view: () => ({
          update: (view, previous) => {
            if (applying || destroyed || view.state.doc.eq(previous.doc)) return;
            const markdown = ctx.get(serializerCtx)(view.state.doc);
            if (markdown !== currentValue) {
              currentValue = markdown;
              // Publish synchronously: Save or closing a view must see the latest keystroke.
              onChange(markdown);
            }
          },
        }),
      }),
  );
  const instance = Editor.make()
    .config((ctx) => {
      ctx.set(rootCtx, root);
      ctx.set(defaultValueCtx, value);
      ctx.update(editorViewOptionsCtx, (options) => ({
        ...options,
        attributes: { role: "textbox", "aria-label": label, "aria-multiline": "true" },
        handleDOMEvents: {
          focus: () => {
            onFocus?.();
            return false;
          },
          click: (_view, event) => {
            if ((event.target as Element | null)?.closest("a")) event.preventDefault();
            return false;
          },
        },
      }));
    })
    .use(commonmark)
    .use(gfm)
    .use(history)
    .use(clipboard)
    .use(changes);
  await instance.create();
  return {
    editor: instance,
    setValue(next: string) {
      if (destroyed || next === currentValue) return;
      instance.action((ctx) => {
        const view = ctx.get(editorViewCtx);
        const doc = ctx.get(parserCtx)(next);
        currentValue = next;
        if (view.state.doc.eq(doc)) return;
        applying = true;
        try {
          // An external buffer update starts a new local history, just as Monaco setValue does.
          // Keep this view's cursor near its previous position instead of stealing another view's.
          view.updateState(
            EditorState.create({
              schema: view.state.schema,
              plugins: view.state.plugins,
              doc,
              selection: TextSelection.near(
                doc.resolve(Math.min(view.state.selection.head, doc.content.size)),
              ),
            }),
          );
        } finally {
          applying = false;
        }
      });
    },
    async destroy() {
      if (destroyed) return;
      destroyed = true;
      await instance.destroy();
    },
  };
}

export type MarkdownEditorController = Awaited<ReturnType<typeof createMarkdownEditor>>;
