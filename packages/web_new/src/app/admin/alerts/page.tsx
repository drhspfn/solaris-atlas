import { redirect } from "next/navigation";

export default function AdminAlertsPage() {
  redirect("/console/alerts/list");
}
