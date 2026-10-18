#pragma once

#include "core_types.h"
#include "raster_decals.h"
#include "scene_view.h"
#include "shape_geometry_store.h"

#include <QtCore>
#include <QtGui>
#include <QtOpenGL>

#include <memory>

class QOpenGLFunctions;

namespace gui {

class NativeShapeRenderer {
public:
    NativeShapeRenderer();
    ~NativeShapeRenderer();

    void initialize();
    void release();
    bool isInitialized() const;
    void uploadGeometry(const ShapeGeometryStore &geometry);
    void render(const fls::Project &project,
                const ShapeGeometryStore &geometry,
                const QTransform &worldToScreen,
                const QSize &size,
                const QSet<QString> &flashingLayerIds,
                double flashHue,
                double flashStrength,
                bool clearBackground = true);
    void render(const QVector<SceneRenderEntry> &entries,
                const ShapeGeometryStore &geometry,
                const QTransform &worldToScreen,
                const QSize &size,
                const QSet<QString> &flashingLayerIds,
                double flashHue,
                double flashStrength,
                bool clearBackground = true);

    GLuint renderSceneToTexture(const fls::Project &project,
                                const ShapeGeometryStore &geometry,
                                const QTransform &worldToScreen,
                                const QSize &size);
    GLuint renderScenesToTexture(const QVector<fls::Project> &projects,
                                 const QVector<QRect> &clipRects,
                                 const ShapeGeometryStore &geometry,
                                 const QTransform &worldToScreen,
                                 const QSize &size,
                                 bool preserveExisting = false);

private:
    struct ShapeRange {
        int firstVertex = 0;
        int vertexCount = 0;
    };

    void setUniformRows(int row0Location, int row1Location, const QTransform &transform);
    ShapeRange fallbackRange(int shapeId, const ShapeGeometryStore &geometry);
    bool drawSceneToFbo(QOpenGLFunctions *functions,
                        const fls::Project &project,
                        const ShapeGeometryStore &geometry,
                        const QTransform &worldToScreen,
                        const QSize &size);
    bool drawProjectLayers(QOpenGLFunctions *functions,
                           const fls::Project &project,
                           const ShapeGeometryStore &geometry,
                           const QTransform &worldToScreen,
                           const QSize &size);
    bool drawRenderEntries(QOpenGLFunctions *functions,
                           const QVector<SceneRenderEntry> &entries,
                           const ShapeGeometryStore &geometry,
                           const QTransform &worldToScreen,
                           const QSize &size);
    bool drawRasterLayer(QOpenGLFunctions *functions,
                         quint32 rasterId,
                         const std::array<quint8, 4> &color,
                         bool mask,
                         const QSize &fallbackSize,
                         const QTransform &world);
    bool ensureRasterPackLoaded();
    GLuint ensureRasterTexture(QOpenGLFunctions *functions, quint32 rasterId);
    void ensureSceneFramebuffer(const QSize &size);
    // FH6-LOCAL PATCH (atlas anti-aliasing): the vinyl atlas used to be rasterised with hard
    // coverage edges, so canvas resolution alone decided how blocky the artwork looked. The scene
    // renders into a 4x-multisampled target that is resolved into the atlas, which anti-aliases
    // every shape edge without touching the resolution.
    // FH6-LOCAL PATCH (tiled atlas MSAA): that target is allocated per *tile* rather than for the
    // whole canvas. A full-canvas target at 4x (8192x4096 RGBA8 x4) costs ~512 MB of VRAM, which
    // is what limited how many preview windows could be open; rendering the same scene once per
    // tile with an offset transform and resolving each tile into its own atlas region produces the
    // same image (same per-pixel sample count, same blend order) for a fraction of the memory.
    bool multisampleAllowed(const QSize &size) const;
    QSize sceneTileSize(const QSize &canvas) const;
    QOpenGLFramebufferObject *beginScenePass(QOpenGLFunctions *functions, const QSize &passSize,
                                            bool multisample);
    // targetRect = region of the atlas the resolved tile lands in (empty = the whole atlas).
    void endScenePass(QOpenGLFunctions *functions, const QRect &targetRect = QRect());
    void verifyAtlasContent(QOpenGLFunctions *functions, const QSize &size);
    void compositeScene(QOpenGLFunctions *functions, const QSize &size, bool clearBackground);

    QOpenGLShaderProgram program_;
    QOpenGLShaderProgram rasterProgram_;
    QOpenGLShaderProgram compositeProgram_;
    QOpenGLVertexArrayObject vao_;
    QOpenGLVertexArrayObject rasterVao_;
    QOpenGLVertexArrayObject compositeVao_;
    QOpenGLBuffer vertexBuffer_;
    QOpenGLBuffer rasterBuffer_;
    QOpenGLBuffer compositeBuffer_;
    QHash<int, ShapeRange> ranges_;
    QVector<float> vertices_;
    std::unique_ptr<QOpenGLFramebufferObject> sceneFbo_;
    QSize sceneFboSize_;
    // Tile-sized multisample target: reused across tiles and rebuilt only when the tile size
    // changes. Peak VRAM is tileW x tileH x 4 bytes x samples instead of the whole canvas.
    std::unique_ptr<QOpenGLFramebufferObject> sceneMultisampleFbo_;
    bool initialized_ = false;
    bool geometryUploaded_ = false;

    int viewportLocation_ = -1;
    int cameraRow0Location_ = -1;
    int cameraRow1Location_ = -1;
    int worldRow0Location_ = -1;
    int worldRow1Location_ = -1;
    int tintLocation_ = -1;
    int rasterViewportLocation_ = -1;
    int rasterCameraRow0Location_ = -1;
    int rasterCameraRow1Location_ = -1;
    int rasterWorldRow0Location_ = -1;
    int rasterWorldRow1Location_ = -1;
    int rasterTintLocation_ = -1;
    int rasterTextureLocation_ = -1;
    int compositeTextureLocation_ = -1;
    fls::RasterDecalPack rasterPack_;
    QString rasterPackError_;
    QHash<quint32, GLuint> rasterTextures_;
    QHash<quint32, QSize> rasterTextureSizes_;
};

} // namespace gui
