"""Reject crossing and overlapping non-adjacent polygon segments."""
def polygon_simple(points):
    def orientation(a,b,c):
        return (b['longitude']-a['longitude'])*(c['latitude']-a['latitude'])-(b['latitude']-a['latitude'])*(c['longitude']-a['longitude'])
    def on(a,b,c):
        return min(a['latitude'],b['latitude'])-1e-10<=c['latitude']<=max(a['latitude'],b['latitude'])+1e-10 and min(a['longitude'],b['longitude'])-1e-10<=c['longitude']<=max(a['longitude'],b['longitude'])+1e-10
    def crossing(a,b,c,d):
        values=[orientation(a,b,c),orientation(a,b,d),orientation(c,d,a),orientation(c,d,b)]
        if values[0]*values[1]<0 and values[2]*values[3]<0:return True
        return any(abs(value)<1e-10 and on(x,y,z) for value,x,y,z in [(values[0],a,b,c),(values[1],a,b,d),(values[2],c,d,a),(values[3],c,d,b)])
    for i,a in enumerate(points):
        b=points[(i+1)%len(points)]
        for j,c in enumerate(points):
            if j<=i or j==(i+1)%len(points) or i==(j+1)%len(points):continue
            if crossing(a,b,c,points[(j+1)%len(points)]):return False
    return True
