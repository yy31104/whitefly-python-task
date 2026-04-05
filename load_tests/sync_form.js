import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 10,
  duration: "20s",
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<1000"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080/flask";

export default function () {
  const unique = `${__VU}-${__ITER}`;
  const payload = {
    first_name: "Load",
    last_name: "Sync",
    email: `sync-${unique}@example.com`,
    honeypot: "",
  };

  const response = http.post(`${BASE_URL}/sync-form`, payload, {
    redirects: 0,
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
  });

  check(response, {
    "sync submission redirected": (r) => r.status === 302 || r.status === 303,
  });

  sleep(0.5);
}
