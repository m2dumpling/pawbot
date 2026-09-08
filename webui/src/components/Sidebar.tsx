import {
  type ReactNode,
  type RefObject,
  useRef,
  useState,
} from "react";
import {
  Archive,
  Brain,
  CalendarClock,
  Menu,
  Plus,
  Search,
  Settings,
  Blocks,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  ChatList,
  type SidebarDeleteItem,
  type SidebarPaneGroup,
} from "@/components/ChatList";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import {
  SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
  SidebarSelectionHighlight,
} from "@/components/SidebarSelectionHighlight";
import { Button } from "@/components/ui/button";
import type {
  ChatSummary,
  SidebarViewState,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface SidebarProps {
  sessions: ChatSummary[];
  temporarySessions?: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  newChatActive: boolean;
  onNewChat: () => void;
  onSelect: (key: string) => void;
  onCloseTemporaryChat?: (key: string) => void;
  onRequestDelete: (key: string, label: string) => void;
  onRequestDeleteMany?: (items: SidebarDeleteItem[]) => void;
  onTogglePin: (key: string) => void;
  onRequestRename: (key: string, label: string) => void;
  onRequestRenameTab?: (key: string, label: string) => void;
  onToggleArchive: (key: string) => void;
  paneGroups?: Record<string, SidebarPaneGroup>;
  onSelectPane?: (tabKey: string, paneKey: string) => void;
  onCreateTab?: (paneKey: string) => void;
  onDetachPane?: (tabKey: string, paneKey: string) => void;
  onDissolveTab?: (tabKey: string) => void;
  onAttachPane?: (
    paneKey: string,
    tabKey: string,
  ) => void;
  onToggleGroup: (groupId: string) => void;
  onRequestRenameProject: (projectKey: string, label: string) => void;
  onNewChatInProject: (projectPath: string, projectName: string) => void;
  onOpenSettings: () => void;
  onOpenApps: () => void;
  onOpenSkills: () => void;
  onOpenAutomations: () => void;
  onSettingsIntent?: () => void;
  onOpenSearch: () => void;
  activeUtility?: "apps" | "skills" | "automations" | null;
  onToggleArchived: () => void;
  onCollapse: () => void;
  onExpand?: () => void;
  containActionMenus?: boolean;
  collapsed?: boolean;
  pinnedKeys?: string[];
  archivedKeys?: string[];
  pinnedPaneKeys?: string[];
  archivedPaneKeys?: string[];
  sessionOrder?: string[];
  titleOverrides?: Record<string, string>;
  projectNameOverrides?: Record<string, string>;
  collapsedGroups?: Record<string, boolean>;
  runningChatIds?: string[];
  updatedChatIds?: string[];
  recoveryChatIds?: string[];
  viewState?: SidebarViewState;
  showArchived?: boolean;
  archivedCount?: number;
  defaultWorkspacePath?: string | null;
  hostChromeInset?: boolean;
}

type NavigatorWithUserAgentData = Navigator & {
  userAgentData?: { platform?: string };
};

function isApplePlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  const platform = navigator.platform || "";
  const userAgentPlatform =
    (navigator as NavigatorWithUserAgentData).userAgentData?.platform || "";
  return /mac|iphone|ipad|ipod/i.test(`${platform} ${userAgentPlatform}`);
}

function newChatShortcutLabel(): string {
  return isApplePlatform() ? "⌘⇧O" : "Ctrl+Shift+O";
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation();
  const [menuPortalContainer, setMenuPortalContainer] =
    useState<HTMLElement | null>(null);
  const collapsed = Boolean(props.collapsed);
  const toggleLabel = t("thread.header.toggleSidebar");
  const newChatShortcut = newChatShortcutLabel();
  const activeActionRef = useRef<HTMLButtonElement>(null);
  const activeUtilityRef = useRef<HTMLButtonElement>(null);
  const activeUtilityId = props.activeUtility
    ? `utility:${props.activeUtility}`
    : null;

  return (
    <nav
      ref={props.containActionMenus ? setMenuPortalContainer : undefined}
      aria-label={t("sidebar.navigation")}
      className={cn(
        "relative flex h-full w-full min-w-0 flex-col border-r border-sidebar-border/70 text-sidebar-foreground",
        props.hostChromeInset ? "bg-transparent" : "bg-sidebar",
      )}
    >
      {/* Brand row: paw mark + product name + collapse */}
      <div
        className={cn(
          "flex items-center gap-2 border-b border-sidebar-border/55 px-3 pb-3",
          props.hostChromeInset ? "pt-[2.85rem]" : "pt-4",
          collapsed ? "w-14 justify-center px-0" : "justify-between",
        )}
      >
        <button
          type="button"
          aria-label={collapsed ? toggleLabel : undefined}
          aria-hidden={collapsed ? undefined : true}
          title={collapsed ? toggleLabel : undefined}
          onClick={collapsed ? props.onExpand : undefined}
          tabIndex={collapsed ? 0 : -1}
          className={cn(
            "flex h-9 shrink-0 items-center gap-2 overflow-hidden rounded-[0.85rem] transition-colors",
            collapsed
              ? "w-9 justify-center hover:bg-sidebar-accent/75"
              : "pointer-events-none -ml-1",
          )}
        >
          <img
            src="/brand/pawbot_mark.svg"
            alt=""
            className="h-8 w-8 select-none rounded-[0.85rem] bg-primary/[0.08] p-1.5 object-contain ring-1 ring-primary/[0.12] drop-shadow-[0_5px_10px_hsl(263_85%_70%/0.2)]"
            draggable={false}
          />
          {!collapsed && (
            <span className="select-none text-[14px] font-semibold tracking-[-0.01em]">
              pawbot
            </span>
          )}
        </button>
        {!collapsed && !props.hostChromeInset && (
          <Button
            variant="ghost"
            size="icon"
            aria-label={t("sidebar.collapse")}
            onClick={props.onCollapse}
            className="h-8 w-8 rounded-[0.7rem] border border-sidebar-border/65 bg-sidebar-accent/35 text-sidebar-foreground/55 hover:bg-sidebar-accent/75 hover:text-sidebar-foreground"
          >
            <Menu className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      {/* Primary action: Start a task */}
      <div className={cn("px-3 pb-2 pt-3", collapsed && "flex w-14 justify-center px-0")}>
        <Button
          ref={props.newChatActive ? activeActionRef : undefined}
          type="button"
          variant={null}
          aria-label={t("sidebar.newChat")}
          aria-current={props.newChatActive ? "page" : undefined}
          aria-keyshortcuts="Meta+Shift+O Control+Shift+O"
          title={`${t("sidebar.newChat")} (${newChatShortcut})`}
          onClick={props.onNewChat}
          className={cn(
            "touch-target h-11 w-full items-center justify-start gap-2.5 rounded-[0.95rem] border border-primary/25 bg-primary/[0.06] px-2.5 font-semibold text-primary shadow-[0_8px_20px_-16px_hsl(var(--primary)/0.8)]",
            "transition-[background-color,border-color,box-shadow,transform] hover:border-primary/40 hover:bg-primary/[0.1] hover:shadow-[0_10px_22px_-15px_hsl(var(--primary)/0.75)] active:scale-[0.99]",
            collapsed && "h-9 w-9 justify-center px-0",
            props.newChatActive &&
              "ring-2 ring-primary/25 ring-offset-2 ring-offset-sidebar",
          )}
        >
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-[0.65rem] bg-primary text-primary-foreground shadow-sm">
            <Plus className="h-4 w-4" strokeWidth={2.25} />
          </span>
          {!collapsed && <span className="text-[13px]">{t("sidebar.newChat")}</span>}
          {!collapsed ? (
            <kbd className="ml-auto rounded-md border border-primary/15 bg-background/75 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary/65">
              {newChatShortcut}
            </kbd>
          ) : null}
        </Button>
      </div>

      {/* Lightweight find entry */}
      <div className={cn("px-3 pb-3", collapsed && "flex w-14 justify-center px-0")}>
        <Button
          type="button"
          variant={null}
          aria-label={t("sidebar.searchAria")}
          title={collapsed ? t("sidebar.searchAria") : undefined}
          onClick={props.onOpenSearch}
          className={cn(
            "h-9 w-full items-center justify-start gap-2 rounded-[0.85rem] border border-sidebar-border/70 bg-background/70 px-2.5 text-[12px] font-normal text-sidebar-foreground/60",
            "transition-[background-color,border-color,color] hover:border-primary/25 hover:bg-background hover:text-sidebar-foreground/90",
            collapsed && "h-8 w-8 justify-center px-0",
          )}
        >
          <Search className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
          {!collapsed && (
            <span className="min-w-0 truncate">{t("sidebar.findPlaceholder")}</span>
          )}
        </Button>
      </div>

      <div
        className={cn(
          "flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-t border-sidebar-border/45 pt-2 transition-opacity duration-200",
          collapsed && "pointer-events-none opacity-0",
        )}
      >
        {!collapsed && (
          <ChatList
            sessions={props.sessions}
            temporarySessions={props.temporarySessions}
            activeKey={props.activeKey}
            loading={props.loading}
            emptyLabel={t("chat.noSessions")}
            onSelect={props.onSelect}
            onCloseTemporaryChat={props.onCloseTemporaryChat}
            onRequestDelete={props.onRequestDelete}
            onRequestDeleteMany={props.onRequestDeleteMany}
            onTogglePin={props.onTogglePin}
            onRequestRename={props.onRequestRename}
            onRequestRenameTab={props.onRequestRenameTab}
            onToggleArchive={props.onToggleArchive}
            paneGroups={props.paneGroups}
            onSelectPane={props.onSelectPane}
            onCreateTab={props.onCreateTab}
            onDetachPane={props.onDetachPane}
            onDissolveTab={props.onDissolveTab}
            onAttachPane={props.onAttachPane}
            onToggleGroup={props.onToggleGroup}
            onRequestRenameProject={props.onRequestRenameProject}
            onNewChatInProject={props.onNewChatInProject}
            pinnedKeys={props.pinnedKeys}
            archivedKeys={props.archivedKeys}
            pinnedPaneKeys={props.pinnedPaneKeys}
            archivedPaneKeys={props.archivedPaneKeys}
            sessionOrder={props.sessionOrder}
            titleOverrides={props.titleOverrides}
            projectNameOverrides={props.projectNameOverrides}
            collapsedGroups={props.collapsedGroups}
            runningChatIds={props.runningChatIds}
            updatedChatIds={props.updatedChatIds}
            recoveryChatIds={props.recoveryChatIds}
            density={props.viewState?.density}
            showPreviews={props.viewState?.show_previews}
            showTimestamps={props.viewState?.show_timestamps}
            sort={props.viewState?.sort}
            showArchived={props.showArchived}
            defaultWorkspacePath={props.defaultWorkspacePath}
            actionMenuPortalContainer={
              props.containActionMenus ? menuPortalContainer : undefined
            }
          />
        )}
      </div>

      {/* Utility cluster: toolbox / prompt packs / scheduled tasks / archived */}
      {!collapsed && (
        <div className="border-t border-sidebar-border/45 px-3 pb-1.5 pt-3">
          <p className="flex select-none items-center gap-2 px-1 pb-1 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-sidebar-foreground/42">
            <span className="h-1.5 w-1.5 rounded-full bg-primary/65" aria-hidden />
            {t("sidebar.toolbox.label")}
          </p>
        </div>
      )}
      <SidebarSelectionHighlight
        targetRef={activeUtilityRef}
        activeId={activeUtilityId}
        scope="utilities"
        className={cn(
          "relative space-y-0.5 px-2 pb-2",
          collapsed && "flex w-14 flex-col items-center px-0",
        )}
      >
        <SidebarActionButton
          collapsed={collapsed}
          label={t("sidebar.apps")}
          onClick={props.onOpenApps}
          onIntent={props.onSettingsIntent}
          active={props.activeUtility === "apps"}
          selectionRef={activeUtilityRef}
          icon={<Blocks className="h-4 w-4" />}
        />
        <SidebarActionButton
          collapsed={collapsed}
          label={t("sidebar.skills.title")}
          onClick={props.onOpenSkills}
          onIntent={props.onSettingsIntent}
          active={props.activeUtility === "skills"}
          selectionRef={activeUtilityRef}
          icon={<Brain className="h-4 w-4" />}
        />
        <SidebarActionButton
          collapsed={collapsed}
          label={t("sidebar.automations", { defaultValue: "Scheduled tasks" })}
          onClick={props.onOpenAutomations}
          onIntent={props.onSettingsIntent}
          active={props.activeUtility === "automations"}
          selectionRef={activeUtilityRef}
          icon={<CalendarClock className="h-4 w-4" />}
        />
        {props.archivedCount ? (
          <SidebarActionButton
            collapsed={collapsed}
            label={props.showArchived ? t("chat.hideArchived") : t("chat.showArchived")}
            onClick={props.onToggleArchived}
            icon={<Archive className="h-4 w-4" />}
          />
        ) : null}
      </SidebarSelectionHighlight>

      <div
        className={cn(
          "flex items-center gap-1 border-t border-sidebar-border/55 bg-sidebar/45 px-3 py-3 text-xs",
          collapsed && "w-14 flex-col px-0",
        )}
      >
        <SidebarActionButton
          collapsed={collapsed}
          label={t("sidebar.settings")}
          onClick={props.onOpenSettings}
          onIntent={props.onSettingsIntent}
          className={collapsed ? undefined : "flex-1"}
          icon={<Settings className="h-4 w-4" />}
        />
        <ConnectionBadge />
      </div>
    </nav>
  );
}

function SidebarActionButton({
  collapsed,
  label,
  icon,
  onClick,
  active = false,
  className,
  shortcut,
  ariaKeyShortcuts,
  onIntent,
  selectionRef,
}: {
  collapsed: boolean;
  label: string;
  icon: ReactNode;
  onClick: () => void;
  active?: boolean;
  className?: string;
  shortcut?: string;
  ariaKeyShortcuts?: string;
  onIntent?: () => void;
  selectionRef?: RefObject<HTMLButtonElement>;
}) {
  const title = shortcut ? `${label} (${shortcut})` : collapsed ? label : undefined;

  return (
    <Button
      ref={active ? selectionRef : undefined}
      type="button"
      variant={null}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      aria-keyshortcuts={ariaKeyShortcuts}
      title={title}
      onClick={() => onClick()}
      onFocus={onIntent}
      onPointerEnter={onIntent}
      className={cn(
        "touch-target group h-8 min-w-0 gap-2 overflow-hidden rounded-xl font-medium",
        SIDEBAR_SELECTION_ACTION_ITEM_CLASS,
        collapsed
          ? "w-9 justify-center gap-0 px-0"
          : "w-full justify-start gap-2 px-3 text-[12.5px]",
        active
          ? "text-sidebar-accent-foreground"
          : "text-sidebar-foreground/80 hover:bg-sidebar-foreground/[0.05] hover:text-sidebar-foreground",
        className,
      )}
    >
      <span
        className={cn(
          "flex shrink-0 items-center justify-center transition-transform duration-300 ease-out",
          collapsed ? "translate-x-0" : "translate-x-0",
        )}
        aria-hidden
      >
        {icon}
      </span>
      <span
        className={cn(
          "min-w-0 overflow-hidden truncate whitespace-nowrap transition-[max-width,opacity,transform] duration-200 ease-out",
          collapsed
            ? "max-w-0 -translate-x-1 opacity-0"
            : "max-w-[12rem] translate-x-0 opacity-100",
        )}
      >
        {label}
      </span>
    </Button>
  );
}
