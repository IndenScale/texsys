import type { ProjectFile } from "./sdk";
export type SearchScope = "all" | "commands" | "files";

export function filterFiles(files: ProjectFile[], query: string): ProjectFile[] {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return files
    .map((file, index) => {
      const path = file.path.toLocaleLowerCase(),
        name = file.name.toLocaleLowerCase();
      let score = 0;
      for (const word of words) {
        if (name === word) score += 100;
        else if (name.startsWith(word)) score += 70;
        else if (name.includes(word)) score += 50;
        else if (path.includes(word)) score += 30;
        else {
          let at = -1;
          for (const letter of word) {
            at = path.indexOf(letter, at + 1);
            if (at < 0) break;
          }
          if (at < 0) return { file, index, score: -1 };
          score += 10;
        }
      }
      return { file, index, score };
    })
    .filter((item) => item.score >= 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((item) => item.file);
}
