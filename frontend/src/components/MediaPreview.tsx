import { useRef, useState } from 'react';
import { Film, RotateCcw } from 'lucide-react';
import { Button, EmptyState } from './primitives';

export function MediaPreview({
  src,
  poster,
  label,
  source = false,
}: {
  src: string;
  poster?: string;
  label: string;
  source?: boolean;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [failed, setFailed] = useState(false);
  function retry() {
    setFailed(false);
    video.current?.load();
  }
  return (
    <div className={`video-stage ${source ? 'video-stage-source' : ''}`}>
      <video
        ref={video}
        src={src}
        controls
        preload="metadata"
        poster={poster}
        aria-label={label}
        onError={() => setFailed(true)}
        onLoadedMetadata={() => setFailed(false)}
        hidden={failed}
      />
      {failed && (
        <EmptyState
          icon={<Film size={26} />}
          title="Preview unavailable"
          description="The media could not be loaded. It may still be preparing, or this browser may not support its format."
          action={
            <Button onClick={retry}>
              <RotateCcw size={15} />
              Retry preview
            </Button>
          }
        />
      )}
    </div>
  );
}
