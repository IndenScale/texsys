import React from "react";

const paths: Record<string, React.ReactNode> = {
  extensions: <path d="M3 3h7v7H3zm0 11h7v7H3zm11 0h7v7h-7zM16.5 2l5.5 5.5-5.5 5.5L11 7.5z" />,
  filter: <path d="M3 4h18v3l-7 7v5l-4 2v-7L3 7z" />,
  star: <path d="m12 3 2.8 5.6 6.2.9-4.5 4.4 1.1 6.1-5.6-2.9L6.4 20l1.1-6.1L3 9.5l6.2-.9z" />,
  settings: (
    <>
      <path d="m10 3-.6 2.4-2 .9-2.2-.7-2 3.4 1.7 1.7v2.6l-1.7 1.7 2 3.4 2.3-.7 2 .9L10 21h4l.6-2.4 2-.9 2.2.7 2-3.4-1.7-1.7v-2.6l1.7-1.7-2-3.4-2.3.7-2-.9L14 3Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  "clear-search": (
    <>
      <path d="M3 5h18M3 10h14M3 15h9m3-1 6 6m0-6-6 6" />
    </>
  ),
  list: <path d="M3 5h18M3 10h18M3 15h13M3 20h10" />,
  replace: (
    <>
      <path d="M4 5h11l4 4-4 4M19 9H6M20 19H9l-4-4 4-4M5 15h13" />
    </>
  ),
  "replace-all": (
    <>
      <path d="M4 4h12l4 4-4 4M20 8H4M20 20H8l-4-4 4-4M4 16h16M2 6V2h4M22 18v4h-4" />
    </>
  ),
  more: (
    <>
      <circle cx="5" cy="12" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </>
  ),
  braces: <path d="M9 3H7v6l-3 3 3 3v6h2M15 3h2v6l3 3-3 3v6h-2" />,
  "file-config": (
    <>
      <path d="M14 3H5v18h14V8l-5-5ZM14 3v5h5M8 12h8M8 16h8M11 10v4M14 14v4" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10" width="14" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" />
    </>
  ),
  image: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="1" />
      <circle cx="8" cy="8" r="2" />
      <path d="m3 17 6-5 4 4 3-3 5 5" />
    </>
  ),
  trash: (
    <>
      <path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" />
    </>
  ),
  edit: (
    <>
      <path d="m4 16 12-12 4 4L8 20H4v-4ZM14 6l4 4" />
    </>
  ),
  compare: (
    <>
      <path d="M9 3H3v18h6M15 3h6v18h-6M7 12h10m-3-3 3 3-3 3" />
    </>
  ),
  files: (
    <>
      <path d="M8 7H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-2" />
      <path d="M9 18h9a1 1 0 0 0 1-1V7l-4-4H9a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1Z" />
      <path d="M15 3v4h4" />
    </>
  ),
  "new-file": (
    <>
      <path d="M13 3H4v18h15V9l-6-6ZM13 3v6h6M8 14h7m-3.5-3.5v7" />
    </>
  ),
  "new-folder": (
    <>
      <path d="M3 7V5h6l2 2h10v13H3ZM9 14h6m-3-3v6" />
    </>
  ),
  collapse: (
    <>
      <rect x="7" y="7" width="14" height="14" rx="1" />
      <path d="M17 3H3v14m8-3h6" />
    </>
  ),
  file: (
    <>
      <path d="M14 3H5v18h14V8Z" />
      <path d="M14 3v5h5" />
    </>
  ),
  plus: <path d="M12 4v16M4 12h16" />,
  minus: <path d="M4 12h16" />,
  tick: <path d="m5 12 4 4L19 6" />,
  back: <path d="M20 12H4m6-6-6 6 6 6" />,
  forward: <path d="M4 12h16m-6-6 6 6-6 6" />,

  branch: (
    <>
      <circle cx="6" cy="5" r="2" />
      <circle cx="18" cy="6" r="2" />
      <circle cx="6" cy="19" r="2" />
      <path d="M6 7v10M18 8c0 6-12 2-12 7" />
    </>
  ),

  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </>
  ),
  folder: <path d="M3 7V5h6l2 2h10v13H3Z" />,
  module: (
    <>
      <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />
      <path d="m12 5 6 3.5v7L12 19l-6-3.5v-7L12 5Zm0 7 6-3.5M6 8.5l6 3.5v7" />
    </>
  ),
  library: <path d="M4 4h4v16H4zm6 0h4v16h-4zm6 1 4-1 4 15-4 1z" />,
  logo: (
    <>
      <path d="M3 3l4 4m0 0h7m-7 0v7" />
      <path d="M13 13h8m-4 0v8" />
    </>
  ),
  system: (
    <>
      <rect x="3" y="3" width="6" height="6" rx="1" />
      <rect x="15" y="15" width="6" height="6" rx="1" />
      <path d="M9 6h9v9M6 9v9h9" />
    </>
  ),
  cube: (
    <>
      <path d="m12 2 9 5v10l-9 5-9-5V7Z" />
      <path d="m3 7 9 5 9-5M12 12v10M7.5 4.5l9 5" />
    </>
  ),
  table: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 10h18M3 15h18M10 4v16" />
    </>
  ),
  chart: (
    <>
      <path d="M3 3v18h18M6 16l4-7 4 4 6-9" />
    </>
  ),
  check: (
    <>
      <path d="m8 12 3 3 6-7" />
      <path d="M12 2 4 5v7c0 5 8 9 8 9s8-4 8-9V5Z" />
    </>
  ),
  code: (
    <>
      <path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18" />
    </>
  ),
  play: <path d="m8 4 12 8-12 8Z" />,
  "skip-back": <path d="M5 5v14m14-14L8 12l11 7Z" />,
  "skip-forward": <path d="M19 5v14M5 5l11 7-11 7Z" />,
  pause: (
    <>
      <path d="M8 4v16M16 4v16" />
    </>
  ),
  chevron: <path d="m8 10 4 4 4-4" />,
  "scm-chevron": <path d="m5 9 7 7 7-7" />,
  "scm-open-file": <path d="M9 4h7l5 5v11H9M16 4v5h5M9 7H3v6m0 0 3-3m-3 3-2-3" />,
  sparkles: (
    <path d="M10 2c0 5-2 7-7 8 5 1 7 3 7 8 1-5 3-7 8-8-5-1-7-3-8-8Zm9 12c0 3-1 4-4 5 3 0 4 1 4 4 1-3 2-4 5-4-3-1-4-2-5-5Z" />
  ),
  search: (
    <>
      <circle cx="10" cy="10" r="6" />
      <path d="m15 15 6 6" />
    </>
  ),
  "search-plus": (
    <>
      <circle cx="10" cy="10" r="6" />
      <path d="m15 15 6 6M7 10h6m-3-3v6" />
    </>
  ),
  locate: (
    <>
      <circle cx="12" cy="12" r="6" />
      <path d="M12 2v4m0 12v4M2 12h4m12 0h4" />
      <circle cx="12" cy="12" r="1" />
    </>
  ),
  close: <path d="m6 6 12 12M6 18 18 6" />,
  arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  refresh: (
    <>
      <path d="M20 10a8 8 0 1 0-1 7M20 3v7h-7" />
    </>
  ),
  fit: (
    <>
      <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />
      <rect x="7" y="7" width="10" height="10" rx="1" />
    </>
  ),
  layers: (
    <>
      <path d="m12 2 10 6-10 6L2 8Zm-9 11 9 5 9-5M3 18l9 5 9-5" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
    </>
  ),
  save: (
    <>
      <path d="M3 3h15l3 3v15H3Z" />
      <path d="M7 3v6h10V3M7 21v-8h10v8" />
    </>
  ),
  undo: (
    <>
      <path d="M4 10h10a6 6 0 0 1 0 12M9 5l-5 5 5 5" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 6v6l4 2" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7h.01" />
    </>
  ),
  terminal: (
    <>
      <rect x="2.5" y="4.5" width="19" height="15" rx="2.5" />
      <path d="m6.5 8.5 3.5 3.5-3.5 3.5m5 0h4" />
    </>
  ),
  "chevron-up": <path d="m6 14 6-6 6 6" />,
  "chevron-down": <path d="m6 10 6 6 6-6" />,
  "chevron-right": <path d="m9 6 6 6-6 6" />,
  "chevron-left": <path d="m15 6-6 6 6 6" />,
  "split-horizontal": (
    <>
      <rect x="3.5" y="3.5" width="17" height="17" rx="1.5" />
      <path d="M12 3.5v17" />
    </>
  ),
  preview: (
    <>
      <rect x="4" y="3" width="16" height="18" rx="2" />
      <path d="M8 7h8M8 11h8M8 15h4" />
      <rect x="14" y="15" width="2" height="2" rx="0.5" />
    </>
  ),
  "preview-beside": (
    <>
      <rect x="3" y="3.5" width="18" height="17" rx="1.5" />
      <path d="M11 3.5v17M14 7.5h4M14 11.5h4M14 15.5h2M6 8l2 2-2 2" />
    </>
  ),
  "terminal-tab": (
    <>
      <path d="m4 8 3 3-3 3m4 0h4" />
    </>
  ),
  "panel-maximize": (
    <>
      <path d="M3.5 8.5V3.5h5M15.5 3.5h5v5M3.5 15.5v5h5M20.5 15.5v5h-5" />
      <rect x="7.5" y="7.5" width="9" height="9" rx="1.5" />
    </>
  ),
  "panel-restore": (
    <>
      <path d="M8 3.5v4.5H3.5M16 3.5v4.5h4.5M8 20.5v-4.5H3.5M16 20.5v-4.5h4.5" />
      <rect x="8" y="8" width="8" height="8" rx="1.5" />
    </>
  ),
  "error-circle": (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m9 9 6 6m0-6-6 6" />
    </>
  ),
  bell: <path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zm5 3a2 2 0 0 0 4 0" />,
  keyboard: <path d="M3 6h18v12H3zm3 4h1m3 0h1m3 0h1m3 0h1M6 14h1m3 0h5m3 0h1" />,
  remote: <path d="m13 3-5 5 5 5m-2-2 5 5-5 5" />,
  warning: (
    <>
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <path d="M12 9v4M12 17h.01" />
    </>
  ),
  snow: (
    <>
      <path d="M12 2v20M3.34 7l17.32 10M3.34 17 20.66 7M9 4l3 3 3-3M9 20l3-3 3 3M3.5 10.5l4-1-1-4M20.5 13.5l-4 1 1 4M6.5 18.5l1-4-4-1M17.5 5.5l-1 4 4 1" />
    </>
  ),
};
const panelDividers: Record<string, { open: string; closed: string }> = {
  sidebar: { open: "M9 4v16", closed: "M6 8v8" },
  "bottom-panel": { open: "M3 14h18", closed: "M7 17h10" },
  inspector: { open: "M15 4v16", closed: "M18 8v8" },
};
export function Icon({
  name,
  size = 16,
  strokeWidth = 1.5,
  expanded = true,
}: {
  name: string;
  size?: number;
  strokeWidth?: number;
  expanded?: boolean;
}) {
  const panel = panelDividers[name];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {panel ? (
        <>
          <rect x="3" y="4" width="18" height="16" rx="2" />
          <path d={expanded ? panel.open : panel.closed} />
        </>
      ) : (
        (paths[name] ?? paths.grid)
      )}
    </svg>
  );
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="studio-empty">
      <div className="empty-icon">
        <Icon name="layers" size={28} />
      </div>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
