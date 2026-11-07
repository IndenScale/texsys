import React from "react";

const paths: Record<string, React.ReactNode> = {
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
      <path d="M8 3h9l4 4v14H8Z" />
      <path d="M17 3v5h4M4 5H2v13h3" />
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
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m6 8 4 4-4 4m7 0h5" />
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
  size = 18,
  expanded = true,
}: {
  name: string;
  size?: number;
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
      strokeWidth="1.65"
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
