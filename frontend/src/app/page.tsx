import { BackendStatus } from "@/components/backend-status";

export default function Home() {
  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">LOCAL-FIRST ADAPTIVE LEARNING</p>
        <h1>LearnLoop</h1>
        <p className="subtitle">
          从目标、练习到复习，建立可以持续调整的个人学习闭环。
        </p>
        <BackendStatus />
      </section>
    </main>
  );
}
