import { redirect } from "next/navigation";

export default function AdminUsagePage() {
  redirect("/console/usage/list");
}
