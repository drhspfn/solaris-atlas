import type { Metadata } from "next";
import { connection } from "next/server";
import { serverApi } from "@/api/serverApi";
import {
  CutsceneArchive,
  type CutscenePage,
} from "@/components/story/CutsceneArchive";

export const metadata: Metadata = {
  title: "Cutscene archive",
  description:
    "Watch Wuthering Waves cutscenes and find their source-linked quests.",
};

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();
  const params = await searchParams;
  const query = new URLSearchParams();
  for (const key of ["q", "game_version", "playable", "offset", "locale"]) {
    if (typeof params[key] === "string") query.set(key, params[key]);
  }
  const [initial, releases] = await Promise.all([
    serverApi<CutscenePage>(`/cutscenes?${query}`),
    serverApi<{ game_version: string }[]>("/releases"),
  ]);
  return (
    <CutsceneArchive
      initial={initial}
      versions={[...new Set((releases || []).map((row) => row.game_version))]}
    />
  );
}
