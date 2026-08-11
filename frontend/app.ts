type WorkspaceId = "overview" | "atlas" | "schedules" | "units" | "activity";

const workspace = (document.body.dataset.workspace ?? "atlas") as WorkspaceId;

// Focus the workspace search with Ctrl/Cmd+K in every workspace.
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    const search = document.querySelector<HTMLInputElement>("#search");
    search?.focus();
  }
});

async function boot(): Promise<void> {
  switch (workspace) {
    case "overview": {
      const { initOverview } = await import("./overview");
      initOverview();
      break;
    }
    case "atlas": {
      const { initAtlas } = await import("./atlas");
      initAtlas();
      break;
    }
    case "schedules": {
      const { initSchedules } = await import("./schedules");
      initSchedules();
      break;
    }
    case "units": {
      const { initUnits } = await import("./units");
      initUnits();
      break;
    }
    case "activity": {
      const { initActivity } = await import("./activity");
      initActivity();
      break;
    }
  }
}

void boot();
