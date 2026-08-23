export type ApiCamera = {
  id: string;
  name: string;
  ip: string;
  direction: "entry" | "exit";
  relay_id: string;
  enabled: boolean;
  online: boolean;
};

export type ApiEvent = {
  id: string;
  model: string | null;
  color: string | null;
  full_name: string | null;
  trans_time: string;
  direction: "entry" | "exit";
  two: string;
  three: string;
  city: string;
  alpha: string;
  plate_number: string;
  camera_ip: string;
  access: boolean;
  log_id: string;
  plate_image_url?: string;
  vehicle_image_url?: string;
  unit?: string | null;
};

export type TodayStats = { entry: number; exit: number; total: number };

export type ApiOwner = {
  id: number; full_name: string; model: string | null; color: string | null;
  two: string; three: string; city: string; alpha: string; access: boolean;
  unit: string | null; access_type: number | null; description: string | null;
};

const base = (process.env.NEXT_PUBLIC_RAHBAND_API_URL ?? "").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) throw new Error(`API ${response.status}`);
  return response.json() as Promise<T>;
}

export const api = {
  cameras: () => request<ApiCamera[]>("/api/v1/cameras"),
  events: () => request<ApiEvent[]>("/api/v1/events?limit=20"),
  stats: () => request<TodayStats>("/api/v1/stats/today"),
  owners: () => request<ApiOwner[]>("/api/v1/owners?limit=200"),
  cameraSnapshotUrl: (cameraId: string, version: number) =>
    `${base}/api/v1/cameras/${encodeURIComponent(cameraId)}/snapshot?v=${version}`,
  createOwner: (owner: {
    full_name: string; model: string; color: string; unit: string;
    two: string; alpha: string; three: string; city: string; access: boolean;
  }, token: string) => request<{ id: number; access: boolean }>("/api/v1/owners", {
    method: "POST", body: JSON.stringify(owner), headers: { "X-Control-Token": token },
  }),
  triggerRelay: () => request<{ triggered: boolean; extended: boolean; deadline: string }>("/api/v1/relay/trigger", { method: "POST" }),
  eventSocketUrl: () => {
    const origin = base || window.location.origin;
    return `${origin.replace(/^http/, "ws")}/api/v1/ws/events`;
  },
};
