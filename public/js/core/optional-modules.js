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
    name: "assistant-bot",
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
];

export async function loadOptionalModules(logger = console) {
  const results = await Promise.allSettled(
    OPTIONAL_MODULES.map(async (mod) => {
      await mod.load();
      return mod.name;
    }),
  );

  results.forEach((result, index) => {
    if (result.status === "rejected") {
      logger.error(`Optional module failed: ${OPTIONAL_MODULES[index].name}`, result.reason);
    }
  });
}
