import React, { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { t } from "./i18n";
import "@xterm/xterm/css/xterm.css";

export type TerminalProfile = "default" | "zsh" | "bash";
export type TerminalInfo = { pid: number; shell: string; commandLine: string };
export type TerminalSession = {
  id: string;
  name?: string;
  profile: TerminalProfile;
  command?: string;
  info?: TerminalInfo;
};
export type TerminalControls = Pick<
  Terminal,
  "clear" | "focus" | "getSelection" | "paste" | "scrollLines" | "selectAll"
>;

type Props = {
  session: TerminalSession;
  visible: boolean;
  focused: boolean;
  split: boolean;
  fontSize: number;
  controls: Map<string, TerminalControls>;
  onFocus: () => void;
  onReady: (id: string, info: TerminalInfo) => void;
  onContextMenu: (event: React.MouseEvent) => void;
};

export function TerminalPane({
  session: { id, profile, command },
  visible,
  focused,
  split,
  fontSize,
  controls,
  onFocus,
  onReady,
  onContextMenu,
}: Props) {
  const [opened, setOpened] = useState(false);
  const [status, setStatus] = useState("");
  const viewport = useRef<HTMLDivElement>(null);
  const emulator = useRef<HTMLDivElement>(null);
  const view = useRef<{ terminal: Terminal; fit: () => void } | null>(null);

  useEffect(() => {
    if (visible) setOpened(true);
  }, [visible]);

  useEffect(() => {
    const container = emulator.current;
    const parent = viewport.current;
    if (!opened || !container || !parent) return;
    setStatus(t("正在连接终端…"));
    const styles = getComputedStyle(parent);
    const terminal = new Terminal({
      cursorBlink: true,
      disableStdin: true,
      fontFamily: styles.fontFamily,
      fontSize: Number.parseFloat(styles.fontSize),
      scrollback: 3000,
      theme: {
        background: styles.backgroundColor,
        foreground: styles.color,
        cursor: styles.color,
        selectionBackground: "#2d91804d",
      },
    });
    const addon = new FitAddon();
    terminal.loadAddon(addon);
    terminal.open(container);
    const fit = () => {
      if (container.clientWidth && container.clientHeight) addon.fit();
    };
    const observer = new ResizeObserver(fit);
    observer.observe(container);
    view.current = { terminal, fit };
    controls.set(id, terminal);
    fit();

    let socket: WebSocket | undefined;
    let ready = false;
    let completed = false;
    let disposed = false;
    const send = (data: unknown) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data));
    };
    const write = (data: Uint8Array) => {
      if (!ready || socket?.readyState !== WebSocket.OPEN) return;
      if (socket.bufferedAmount + data.length > 128 * 1024) {
        setStatus(t("终端输入过多，请分段粘贴。"));
        return;
      }
      for (let offset = 0; offset < data.length; offset += 16 * 1024) {
        socket.send(data.slice(offset, offset + 16 * 1024));
      }
      setStatus("");
    };
    const encoder = new TextEncoder();
    terminal.onData((data) => write(encoder.encode(data)));
    terminal.onBinary((data) => write(Uint8Array.from(data, (char) => char.charCodeAt(0))));
    terminal.onResize(({ cols, rows }) => {
      if (ready) send({ type: "resize", cols, rows });
    });
    terminal.parser.registerOscHandler(7777, (data) => {
      if (data !== "texsys-command-done") return false;
      window.dispatchEvent(new Event("texsys:terminal:completed"));
      return true;
    });
    const token = document.querySelector<HTMLMetaElement>('meta[name="tex-studio-token"]')?.content;
    if (token) {
      const url = new URL("/api/terminal/ws", window.location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(url, ["texsys-terminal-v1", token]);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => {
        fit();
        send({ type: "start", shell: profile, command, cols: terminal.cols, rows: terminal.rows });
      };
      socket.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          const data = new Uint8Array(event.data);
          terminal.write(data, () => {
            if (!disposed) send({ type: "ack", bytes: data.length });
          });
          return;
        }
        const message = JSON.parse(event.data);
        if (message.type === "ready") {
          ready = true;
          terminal.options.disableStdin = false;
          onReady(id, { pid: message.pid, shell: message.shell, commandLine: message.commandLine });
          setStatus("");
          send({ type: "resize", cols: terminal.cols, rows: terminal.rows });
        } else if (message.type === "exit") {
          completed = true;
          terminal.options.disableStdin = true;
          setStatus(t("终端已退出（{code}）。", { code: message.code }));
          window.dispatchEvent(new Event("texsys:terminal:completed"));
        } else if (message.type === "error") {
          completed = true;
          setStatus(message.message);
        }
      };
      socket.onclose = () => {
        ready = false;
        terminal.options.disableStdin = true;
        if (!completed) setStatus(t("终端连接已断开，请新建终端。"));
      };
    } else {
      setStatus(t("终端服务不可用。"));
    }

    return () => {
      disposed = true;
      observer.disconnect();
      if (socket) {
        socket.onopen = socket.onmessage = socket.onclose = null;
        send({ type: "close" });
        socket.close();
      }
      controls.delete(id);
      view.current = null;
      terminal.dispose();
    };
  }, [opened, command, controls, id, onReady, profile]);

  useEffect(() => {
    const active = view.current;
    if (!active) return;
    active.terminal.options.fontSize = fontSize;
    if (visible) active.fit();
  }, [opened, fontSize, visible]);

  useEffect(() => {
    if (visible && focused) view.current?.terminal.focus();
  }, [opened, visible, focused]);

  return (
    <div
      className={`terminal-pane ${split ? "is-split" : ""} ${focused ? "is-focused" : ""}`}
      hidden={!visible}
      onFocus={onFocus}
      onContextMenu={onContextMenu}
      data-terminal-session={id}
    >
      <div className="terminal-viewport" style={{ fontSize }} ref={viewport}>
        <div className="terminal-emulator" ref={emulator} />
      </div>
      {status && (
        <div className="terminal-status" role="status">
          {status}
        </div>
      )}
    </div>
  );
}
