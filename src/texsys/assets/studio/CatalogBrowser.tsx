import React, { useRef, useState } from "react";
import { ExplorerTree } from "./ExplorerTree";
import { t } from "./i18n";
import { NavigatorMenu } from "./NavigatorMenu";
import { NavigatorButton } from "./NavigatorPanel";
import { Icon } from "./ui";
import type { TreeEntry } from "./workspace-model";

export type CatalogTags = Record<string, string[]>;
export type CatalogGrouping = { id: string; label: string };
export type CatalogRow = { id: string; name: string; detail?: string; icon: string };
export type CatalogGroup<T> = { id: string; label: string; items: T[] };

const standardDimensions = { floor: "楼层", discipline: "专业", lot: "标段", family: "族" };

export function dimensionLabel(dimension: string) {
  return Object.hasOwn(standardDimensions, dimension)
    ? t(standardDimensions[dimension as keyof typeof standardDimensions])
    : dimension;
}

export function tagGroupings(tags: CatalogTags[], defaults: string[]): CatalogGrouping[] {
  const keys = new Set(defaults);
  for (const item of tags) Object.keys(item).forEach((key) => keys.add(key));
  return [...keys].map((dimension) => ({
    id: "tag:" + dimension,
    label: dimensionLabel(dimension),
  }));
}

export function tagFields(tags: CatalogTags): string[] {
  return Object.entries(tags).flatMap(([dimension, values]) => [
    dimension,
    dimensionLabel(dimension),
    ...values,
  ]);
}

export function matchesCatalog(query: string, ...fields: (string | null | undefined)[]) {
  const normalize = (value: string) => value.normalize("NFKC").toLocaleLowerCase();
  const tokens = normalize(query.trim()).split(/\s+/).filter(Boolean);
  const text = normalize(fields.filter(Boolean).join(" "));
  return tokens.every((token) => text.includes(token));
}

export function groupByTag<T>(
  items: T[],
  dimension: string,
  tags: (item: T) => CatalogTags,
): CatalogGroup<T>[] {
  const values = new Map<string | null, T[]>();
  for (const item of items) {
    const labels = tags(item);
    const assigned = Object.hasOwn(labels, dimension) ? labels[dimension] : [];
    for (const value of assigned.length ? assigned : [null]) {
      const group = values.get(value) ?? [];
      group.push(item);
      values.set(value, group);
    }
  }
  return [...values]
    .sort(([left], [right]) =>
      left === null
        ? 1
        : right === null
          ? -1
          : left.localeCompare(right, undefined, { numeric: true }),
    )
    .map(([value, group]) => ({
      id: value === null ? "untagged" : "value:" + value,
      label: value ?? t("未标注"),
      items: group,
    }));
}

export function CatalogControls({
  searchLabel,
  query,
  setQuery,
  groupings,
  grouping,
  setGrouping,
  onEditTags,
}: {
  searchLabel: string;
  query: string;
  setQuery: (query: string) => void;
  groupings: CatalogGrouping[];
  grouping: string;
  setGrouping: (grouping: string) => void;
  onEditTags?: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const label = groupings.find((item) => item.id === grouping)?.label ?? groupings[0].label;
  const clear = () => {
    setQuery("");
    input.current?.focus();
  };
  return (
    <div className="core-catalog-controls">
      <div className="core-catalog-search">
        <Icon name="search" size={15} />
        <input
          ref={input}
          type="search"
          aria-label={searchLabel}
          placeholder={searchLabel}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && query) {
              event.preventDefault();
              event.stopPropagation();
              clear();
            }
          }}
        />
        {query && <NavigatorButton label={t("清除搜索")} icon="close" onClick={clear} />}
      </div>
      <div className="core-catalog-toolbar">
        <NavigatorMenu
          label={t("分组依据")}
          title={label}
          placement="start"
          triggerClassName="core-catalog-grouping"
          triggerIcon={
            <>
              <Icon name="list" size={14} />
              <span>{label}</span>
              <Icon name="chevron" size={13} />
            </>
          }
          items={groupings.map((item) => ({
            label: item.label,
            checked: item.id === grouping,
            run: () => setGrouping(item.id),
          }))}
        />
        {onEditTags && (
          <NavigatorButton label={t("编辑标签配置")} icon="edit" onClick={onEditTags} />
        )}
      </div>
    </div>
  );
}

export function CatalogGroups<T extends CatalogRow>({
  label,
  groups,
  open,
}: {
  label: string;
  groups: CatalogGroup<T>[];
  open: (row: T) => void;
}) {
  const [collapsed, setCollapsed] = useState(() => new Set<string>());
  const [selected, select] = useState("");
  const expanded = new Set<string>();
  const entries: TreeEntry[] = [];
  const destinations = new Map<string, T>();
  for (const group of groups) {
    if (!group.items.length) continue;
    const path = encodeURIComponent(group.id);
    entries.push({
      path,
      name: group.label,
      detail: String(group.items.length),
      directory: true,
      depth: 0,
      virtual: true,
    });
    if (collapsed.has(path)) continue;
    expanded.add(path);
    for (const item of group.items) {
      const child = path + "/" + encodeURIComponent(item.id);
      entries.push({
        path: child,
        name: item.name,
        detail: item.detail,
        directory: false,
        depth: 1,
        icon: item.icon,
        virtual: true,
      });
      destinations.set(child, item);
    }
  }
  return (
    <ExplorerTree
      label={label}
      entries={entries}
      expanded={expanded}
      selected={selected}
      select={select}
      toggle={(path) =>
        setCollapsed((previous) => {
          const next = new Set(previous);
          if (next.has(path)) next.delete(path);
          else next.add(path);
          return next;
        })
      }
      open={(path) => {
        const item = destinations.get(path);
        if (item) open(item);
      }}
    />
  );
}
