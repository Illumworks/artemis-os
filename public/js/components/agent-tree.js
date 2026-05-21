const HEALTH_ORDER = { warning: 0, healthy: 1, never: 2 };

function titleCase(value) {
  return String(value || "uncategorized")
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function agentId(agent) {
  return agent?.agentId || agent?.id || "";
}

function agentName(agent) {
  return agent?.displayName || agent?.title || agent?.name || agentId(agent) || "Unnamed";
}

function lastRunValue(agent) {
  if (agent && Object.hasOwn(agent, "lastRunAt")) return agent.lastRunAt;
  if (agent && Object.hasOwn(agent, "last_run_at")) return agent.last_run_at;
  return agent?.lastRunAt || agent?.last_run_at || agent?.metrics?.lastRunAt || agent?.metrics?.lastRun || agent?.lastRun || null;
}

export function getAgentHealth(agent) {
  const explicit = String(agent?.health || agent?.metrics?.health || agent?.status || "").toLowerCase();
  const lastRun = lastRunValue(agent);
  if (!lastRun || String(lastRun).toLowerCase() === "never") return "never";
  if (explicit.includes("warn") || explicit.includes("attention") || explicit.includes("error") || explicit.includes("fail")) return "warning";
  return "healthy";
}

export function getAgentTrigger(agent) {
  const schedule = String(agent?.schedule || agent?.trigger || agent?.runtime || agent?.cadence || "").toLowerCase();
  return schedule && !schedule.includes("manual") && !schedule.includes("demand") ? "scheduled" : "manual";
}

export function buildAgentTree(agents = []) {
  const tree = {};
  for (const agent of agents) {
    const id = agentId(agent);
    const parts = id.includes(".") ? id.split(".").filter(Boolean) : [];
    const domain = parts[0] || "personal";
    const subdomain = parts.length >= 3 ? parts[1] : (parts.length === 2 ? "none" : "uncategorized");
    tree[domain] ||= {};
    tree[domain][subdomain] ||= [];
    tree[domain][subdomain].push(agent);
  }
  return tree;
}

function matchesSearch(agent, query) {
  if (!query) return true;
  const haystack = [
    agentName(agent),
    agentId(agent),
    agent?.description,
    agent?.goal,
  ].filter(Boolean).join(" ").toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function matchesFilters(agent, filters = {}) {
  const statuses = filters.statuses || [];
  const triggers = filters.triggers || [];
  if (statuses.length && !statuses.includes(getAgentHealth(agent))) return false;
  if (triggers.length && !triggers.includes(getAgentTrigger(agent))) return false;
  return true;
}

function sortAgents(agents, sort) {
  const copy = [...agents];
  if (sort === "last_run") {
    return copy.sort((a, b) => {
      const av = Date.parse(lastRunValue(a) || "") || 0;
      const bv = Date.parse(lastRunValue(b) || "") || 0;
      return bv - av || agentName(a).localeCompare(agentName(b));
    });
  }
  if (sort === "health") {
    return copy.sort((a, b) => {
      const delta = HEALTH_ORDER[getAgentHealth(a)] - HEALTH_ORDER[getAgentHealth(b)];
      return delta || agentName(a).localeCompare(agentName(b));
    });
  }
  return copy.sort((a, b) => agentName(a).localeCompare(agentName(b)));
}

export function getVisibleAgents(agents = [], { query = "", filters = {}, sort = "name" } = {}) {
  return sortAgents(agents.filter((agent) => matchesSearch(agent, query) && matchesFilters(agent, filters)), sort);
}

export function createAgentTreeView(agents = [], options = {}) {
  const tree = buildAgentTree(agents);
  const domains = Object.keys(tree).sort((a, b) => a.localeCompare(b));
  return domains.map((domain) => {
    const subdomains = Object.keys(tree[domain]).sort((a, b) => a.localeCompare(b));
    return {
      id: domain,
      label: titleCase(domain),
      total: subdomains.reduce((sum, subdomain) => sum + tree[domain][subdomain].length, 0),
      subdomains: subdomains.map((subdomain) => {
        const agentsForSubdomain = tree[domain][subdomain];
        return {
          id: subdomain,
          label: subdomain === "none" ? "None" : titleCase(subdomain),
          total: agentsForSubdomain.length,
          agents: getVisibleAgents(agentsForSubdomain, options),
        };
      }),
    };
  });
}

export function summarizeAgentTree(agents = [], options = {}) {
  const view = createAgentTreeView(agents, options);
  const visible = view.reduce(
    (sum, domain) => sum + domain.subdomains.reduce((inner, subdomain) => inner + subdomain.agents.length, 0),
    0,
  );
  return { domains: view.length, visible, total: agents.length };
}
