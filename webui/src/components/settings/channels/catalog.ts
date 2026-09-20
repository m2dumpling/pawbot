import type { LucideIcon } from "lucide-react";

export type ChannelPresentation = {
  displayName: string;
  initials: string;
  color: string;
  icon?: LucideIcon;
  logoUrl?: string;
  setup?: ChannelCatalogSetupPresentation;
};

export type ChannelSetupPresentation = {
  mode?: "webui" | "credentials" | "connect";
  primaryActionLabel?: string;
  command?: string;
  docsUrl?: string;
  docsLabel?: string;
  docsLogoUrl?: string;
  officialUrl?: string;
  officialLabel?: string;
  summary?: string;
  tryIt?: string;
  steps: string[];
  fields?: ChannelConfigField[];
  manualFields?: ChannelConfigField[];
  actions?: ChannelSetupAction[];
  presets?: ChannelProviderPreset[];
};

type ChannelCatalogSetupPresentation = {
  mode?: "webui" | "credentials" | "connect";
  command?: string;
  docsUrl?: string;
  docsLogoUrl?: string;
  fields?: ChannelFieldPresentation[];
  manualFields?: ChannelFieldPresentation[];
  actions?: ChannelSetupActionDefinition[];
  presets?: ChannelProviderPresetDefinition[];
};

type ChannelFieldPresentation = {
  key: string;
};

type ChannelSetupActionDefinition = Omit<ChannelSetupAction, "label">;

export type ChannelProviderPresetDefinition = Omit<ChannelProviderPreset, "label">;

type ChannelSetupAction = {
  id: string;
  label: string;
  url?: string;
  copyText?: string;
  logoUrl?: string;
};

export type ChannelProviderPreset = {
  id: string;
  label: string;
  values: Record<string, string>;
};

export type ChannelConfigField = {
  key: string;
  label: string;
  placeholder?: string;
  secret?: boolean;
  optional?: boolean;
  help?: string;
  inputType?: "text" | "number";
  defaultValue?: string;
  options?: ChannelConfigOption[];
};

type ChannelConfigOption = {
  value: string;
  label: string;
};

const CHAT_APPS_DOCS_URL = "https://github.com/m2dumpling/pawbot#channels-and-integrations";

export function chatAppGuideUrl(sectionId: string): string {
  // The local channel-docs bundle is intentionally not shipped anymore;
  // keep the section argument for API compatibility and open the public
  // integrations overview instead.
  void sectionId;
  return CHAT_APPS_DOCS_URL;
}

export function docsUrlWithBase(
  url: string | undefined,
  chatAppsDocsUrl?: string,
): string | undefined {
  if (!url || !chatAppsDocsUrl) return url;
  if (!url.startsWith(CHAT_APPS_DOCS_URL)) return url;
  return chatAppsDocsUrl.replace(/\/$/, "");
}
