import { useState, type ReactNode } from "react";
import type { SearchHit } from "../api/client";
import { BrandLogo } from "./BrandLogo";
import { roleLabel } from "../api/client";

const HIT_TYPE: Record<string, string> = {
  startup: "Стартап",
  investor: "Инвестор",
  user: "Пользователь",
  organization: "Организация",
  ticket: "Обращение",
  complaint: "Жалоба",
};

const MOD = ["owner", "administrator", "moderator"];
const SUPPORT = ["owner", "administrator", "support"];
const OWNER = ["owner"];
const ADMIN = ["owner", "administrator"];

const NAV: { id: string; label: string; badgeKey?: string; roles?: string[] }[] = [
  { id: "overview", label: "Обзор" },
  { id: "catalog", label: "Каталог", roles: [...MOD, "support"] },
  { id: "moderation", label: "Модерация", badgeKey: "moderation", roles: MOD },
  { id: "support", label: "Поддержка", badgeKey: "support", roles: SUPPORT },
  { id: "users", label: "Пользователи", roles: [...MOD, "support"] },
  { id: "orgs", label: "Организации", badgeKey: "orgs", roles: [...MOD, "support"] },
  { id: "complaints", label: "Жалобы", badgeKey: "complaints", roles: [...MOD, "support"] },
  { id: "premium", label: "Premium", roles: ADMIN },
  { id: "analytics", label: "Аналитика" },
  { id: "staff", label: "Сотрудники", roles: OWNER },
  { id: "audit", label: "Журнал действий", roles: ADMIN },
  { id: "settings", label: "Настройки" },
];

type Props = {
  section: string;
  onSection: (id: string) => void;
  theme: string;
  onTheme: () => void;
  account: string;
  role: string;
  onLogout: () => void;
  counts?: Record<string, number>;
  searchValue: string;
  onSearchChange: (value: string) => void;
  searchHits: SearchHit[];
  onPickHit: (hit: SearchHit) => void;
  children: ReactNode;
};

export function Layout({
  section,
  onSection,
  theme,
  onTheme,
  account,
  role,
  onLogout,
  counts = {},
  searchValue,
  onSearchChange,
  searchHits,
  onPickHit,
  children,
}: Props) {
  const [open, setOpen] = useState(false);
  const [activeHit, setActiveHit] = useState(-1);

  function select(id: string) {
    onSection(id);
    setOpen(false);
  }

  return (
    <div className="app">
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand">
          <BrandLogo />
          RAUNDO Admin
        </div>
        <nav aria-label="Разделы">
          {NAV.filter((item) => !item.roles || item.roles.includes(role)).map((item) => {
            const count = item.badgeKey ? counts[item.badgeKey] || 0 : 0;
            return (
              <button
                key={item.id}
                type="button"
                className={section === item.id ? "active" : ""}
                aria-current={section === item.id ? "page" : undefined}
                onClick={() => select(item.id)}
              >
                <span>{item.label}</span>
                {count > 0 && <span className="nav-count">{count}</span>}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <div className="sidebar-account">
            <strong>{account}</strong>
            <span className="muted">{roleLabel(role)}</span>
          </div>
          <button type="button" className="ghost" onClick={onLogout}>
            Выйти
          </button>
        </div>
      </aside>
      <div className="main-wrap">
        <header className="topbar">
          <button
            type="button"
            className="icon-button mobile-menu"
            aria-label="Меню"
            onClick={() => setOpen(true)}
          >
            ☰
          </button>
          <div className="search-shell">
            <span className="search-icon" aria-hidden="true">
              ⌕
            </span>
            <input
              id="global-search"
              className="search"
              placeholder="Поиск: стартап, инвестор, пользователь, обращение (Ctrl+K)"
              autoComplete="off"
              value={searchValue}
              onChange={(e) => {
                onSearchChange(e.target.value);
                setActiveHit(-1);
              }}
              onKeyDown={(e) => {
                if (!searchHits.length) return;
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActiveHit((i) => (i + 1) % searchHits.length);
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActiveHit((i) => (i <= 0 ? searchHits.length - 1 : i - 1));
                } else if (e.key === "Enter" && activeHit >= 0) {
                  e.preventDefault();
                  onPickHit(searchHits[activeHit]);
                  setActiveHit(-1);
                } else if (e.key === "Escape") {
                  onSearchChange("");
                }
              }}
            />
            {searchHits.length > 0 && searchValue.trim().length >= 2 && (
              <div className="search-results" role="listbox">
                {searchHits.map((hit, index) => (
                  <button
                    key={`${hit.type}-${hit.id}`}
                    type="button"
                    className={index === activeHit ? "active" : ""}
                    onClick={() => onPickHit(hit)}
                  >
                    <span className="hit-type">{HIT_TYPE[hit.type] ?? "Результат"}</span>
                    <span>{hit.title}</span>
                    {hit.subtitle && <span className="muted small">{hit.subtitle}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="topbar-actions">
            <button type="button" className="ghost small" onClick={onTheme}>
              {theme === "light" ? "Тёмная тема" : "Светлая тема"}
            </button>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
