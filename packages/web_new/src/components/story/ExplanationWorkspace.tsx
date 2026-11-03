import { Link } from "@/lib/router";
import type { StoryExplanation } from "../../data/explanations";
import { ExplanationSources } from "./ExplanationSources";

export function ExplanationWorkspace({
  explanation,
}: {
  explanation: StoryExplanation;
}) {
  const questions = [...(explanation.questions || [])].sort(
    (a, b) => a.priority - b.priority,
  );
  return (
    <>
      {questions.length > 0 && (
        <section
          className="explanation-context"
          aria-label="Story questions and discoveries"
        >
          <h3>Questions and discoveries</h3>
          {questions.map((question) => (
            <article className="explanation-edge" key={question.id}>
              <h4>{question.question}</h4>
              <span className="explanation-badge">
                {question.resolution ? question.status : "Open question"}
                {question.priority <= 1 ? " · High priority" : ""}
              </span>
              {question.resolution && (
                <details>
                  <summary>Later discovery · contains spoilers</summary>
                  <p>{question.resolution.explanation}</p>
                  <ExplanationSources
                    citations={question.resolution.citations}
                    nodes={explanation.nodes}
                  />
                </details>
              )}
            </article>
          ))}
        </section>
      )}
      {!!explanation.sections?.length && (
        <nav className="explanation-context" aria-label="Quest story sections">
          <h3>Inside this quest</h3>
          <p>
            Story sections follow the source reading order; alternatives remain
            separate paths.
          </p>
          <ol>
            {explanation.sections.map((section) => {
              const start = section.citations.find(
                (citation) => citation.node_id === section.start_node_id,
              );
              return (
                <li key={section.key}>
                  <h4>
                    {start?.href ? (
                      <Link to={start.href}>{section.title}</Link>
                    ) : (
                      section.title
                    )}
                  </h4>
                  <p>{section.description}</p>
                  <ExplanationSources
                    citations={section.citations}
                    nodes={explanation.nodes}
                  />
                </li>
              );
            })}
          </ol>
        </nav>
      )}
    </>
  );
}
