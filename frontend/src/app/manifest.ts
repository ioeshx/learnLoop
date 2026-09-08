import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "LearnLoop 自适应学习 Agent",
    short_name: "LearnLoop",
    description: "本地优先、可恢复、可解释的自适应学习 Agent",
    start_url: "/dashboard",
    display: "standalone",
    background_color: "#f3f1ea",
    theme_color: "#246c50",
    icons: [
      {
        src: "/learnloop-icon.svg",
        sizes: "any",
        type: "image/svg+xml",
      },
    ],
  };
}
