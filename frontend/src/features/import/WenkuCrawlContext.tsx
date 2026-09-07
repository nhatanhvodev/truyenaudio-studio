import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

export type WenkuCrawlState = {
  crawling: boolean;
  bookTitle: string;
  startChapter: number;
  endChapter: number;
} | null;

type ContextType = {
  crawlState: WenkuCrawlState;
  setCrawlState: (s: WenkuCrawlState) => void;
};

const WenkuCrawlContext = createContext<ContextType>({
  crawlState: null,
  setCrawlState: () => {},
});

export function WenkuCrawlProvider({ children }: { children: ReactNode }) {
  const [crawlState, setCrawlState] = useState<WenkuCrawlState>(null);
  return (
    <WenkuCrawlContext.Provider value={{ crawlState, setCrawlState }}>
      {children}
    </WenkuCrawlContext.Provider>
  );
}

export function useWenkuCrawl() {
  return useContext(WenkuCrawlContext);
}
