import { isSurfaceAvailable } from './status.js';

const OPTIONAL_MODULES = [
  {
    name: "dev-project-files",
    load: () => import("../panels/dev-project-files.js"),
  },
  {
    name: "mcp-manager",
    load: () => import("../panels/mcp-manager.js"),
  },
  {
    name: "tips-feed",
    load: () => import("../panels/tips-feed.js"),
  },
  {
    // Floating Artemis G2 panel — takes over the FAB when the surface is available.
    // When active, assistant-bot is skipped (skipWhenSurface below).
    name: "floating-artemis",
    surface: "floating-artemis",
    load: () => import("../components/floating-panel.js"),
  },
  {
    // Legacy assistant bot — skipped when the G2 Floating Artemis surface is live.
    name: "assistant-bot",
    skipWhenSurface: "floating-artemis",
    load: () => import("../panels/assistant-bot.js"),
  },
  {
    name: "telegram",
    load: () => import("../features/telegram.js"),
  },
  {
    name: "dev-docs",
    load: () => import("../panels/dev-docs.js"),
  },
  {
    name: "skills-manager",
    load: () => import("../panels/skills-manager.js"),
  },
  {
    name: "integrations",
    load: () => import("../features/integrations.js"),
  },
];

export async function loadOptionalModules(logger = console) {
  const toLoad = OPTIONAL_MODULES.filter((mod) => {
    // Skip this module if the specified surface is currently available
    if (mod.skipWhenSurface && isSurfaceAvailable(mod.skipWhenSurface)) return false;
    return true;
  });

  const results = await Promise.allSettled(
    toLoad.map(async (mod) => {
      await mod.load();
      return mod.name;
    }),
  );

  results.forEach((result, index) => {
    if (result.status === "rejected") {
      logger.error(`Optional module failed: ${toLoad[index].name}`, result.reason);
    }
  });
}
