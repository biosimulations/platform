import { ref } from "vue";

type RafCallback = (time: number) => void;

const subscribers = new Set<RafCallback>();

export function useRafBus() {
  function subscribe(cb: RafCallback) {
    subscribers.add(cb);
    return () => subscribers.delete(cb);
  }

  function tick(time: number) {
    subscribers.forEach(cb => cb(time));
  }

  return { subscribe, tick };
}
