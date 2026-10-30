import { connection } from "next/server";
import { serverApi } from "@/api/serverApi";
import {
  CutsceneRecord,
  type CutsceneDetail,
} from "@/components/story/CutsceneArchive";

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ name: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  await connection();
  const { name } = await params;
  const search = await searchParams;
  const query = new URLSearchParams();
  for (const key of ["game_version", "locale"])
    if (typeof search[key] === "string") query.set(key, search[key]);
  const initial = await serverApi<CutsceneDetail>(
    `/cutscenes/${encodeURIComponent(name)}?${query}`,
  );
  return <CutsceneRecord key={`${name}?${query}`} name={name} initial={initial} />;
}
