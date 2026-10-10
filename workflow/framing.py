"""One authored framing policy shared by animatic and delivered picture."""
POLICY = 'preserve_full_frame_v1'


def filter_for(width, height, policy=None):
    if policy not in (None, POLICY):
        raise ValueError('unknown framing_policy')
    if policy == POLICY:
        return (f'fps=24,scale={width}:{height}:force_original_aspect_ratio=decrease:'
                f'force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1')
    return f'fps=24,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1'
